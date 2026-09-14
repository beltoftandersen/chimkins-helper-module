# models/pricelist_api.py
"""Public entry point for pricelist-computed prices, for the OdooWoo sync.

The middleware cannot reach these prices on its own: product.product has
no `price` field in Odoo 16 (only list_price, lst_price, standard_price
and price_extra), and product.pricelist._get_products_price is private,
which Odoo refuses to expose over XML-RPC -- "Private methods cannot be
called remotely." Hence this public wrapper.
"""

from odoo import _, api, models
from odoo.exceptions import UserError


class ProductPricelist(models.Model):
    _inherit = 'product.pricelist'

    @api.model
    def get_prices_for_skus(self, pricelist_id, skus, quantity=1.0):
        """Return pricelist prices for the given SKUs.

        Returns a list of dicts:
          [{"default_code": "ABC-1", "product_id": 42, "price": 12.34,
            "rule_id": 57}, ...]

        A list is required rather than a dict keyed by product_id, because
        XML-RPC cannot marshal integer dict keys.

        rule_id is the product.pricelist.item that determined the price,
        or False when no rule matched and Odoo fell through to list_price.
        That distinction is the caller's to interpret: on a pricelist of
        per-product rules a fall-through means "this product has no price
        here", while on a pricelist whose global rule is meant to cover
        everything it is perfectly normal.

        Prices exclude tax and are returned unrounded -- the middleware
        handles tax conversion and quantizes to 2dp itself. SKUs with no
        matching product are skipped rather than raising, so one bad SKU
        cannot fail a whole batch.
        """
        if not skus:
            return []

        pricelist = self.env['product.pricelist'].browse(int(pricelist_id)).exists()
        if not pricelist:
            raise UserError(
                _("Pricelist id %s does not exist.") % (pricelist_id,)
            )

        # No company filter on purpose: these pricelists carry
        # company_id = False and the middleware passes no company context,
        # so filtering would match nothing.
        products = self.env['product.product'].search(
            [('default_code', 'in', list(skus))]
        )
        if not products:
            return []

        priced = self._compute_price_rules(pricelist, products, float(quantity))

        # Group by SKU first: two variants can legitimately share a
        # default_code, and the caller should see both rather than one
        # arbitrary winner.
        by_code = {}
        for product in products:
            by_code.setdefault(product.default_code, []).append(product)

        result = []
        seen = set()
        for sku in skus:
            if sku in seen:
                continue
            seen.add(sku)
            for product in by_code.get(sku, ()):
                # Skip rather than defaulting to 0.0 -- a silent zero here
                # would be published to WooCommerce as a real price.
                if product.id in priced:
                    price, rule_id = priced[product.id]
                    result.append({
                        'default_code': sku,
                        'product_id': product.id,
                        'price': price,
                        'rule_id': rule_id,
                    })
        return result

    @api.model
    def _compute_price_rules(self, pricelist, products, quantity):
        """Return {product_id: (price, rule_id)} for the installed version.

        Taken from the price-and-rule API rather than pricing and then
        looking the rule up separately, so the two can never disagree.
        Every compute_price mode is honoured -- fixed, percentage and
        formula, based on list_price or on another pricelist -- and
        rule_id is False where nothing matched.
        """
        if hasattr(pricelist, '_compute_price_rule'):
            computed = pricelist._compute_price_rule(products, quantity)
            # Tolerate a longer tuple: some versions carry extra trailing
            # elements, and only the first two are ours.
            return {
                product_id: (values[0], values[1])
                for product_id, values in computed.items()
            }
        return {
            product.id: tuple(
                pricelist._get_product_price_rule(product, quantity)
            )[:2]
            for product in products
        }
