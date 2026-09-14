# models/price_webhook.py
"""Notify the sync middleware when a price actually changes.

Odoo fires no event the middleware can observe, so without this a price
edit only reaches WooCommerce when somebody clicks a button.

"Actually changes" is meant literally: every trigger below computes the
effective price before and after the write and notifies only the SKUs
whose number moved. A re-import that rewrites identical prices, a form
save that touches nothing, or a rule edit that leaves the result the same
all send nothing.

No pricelist filtering happens here. One Odoo database serves several
dashboards with different settings, so the payload names the pricelist
whose price moved and the middleware decides what to act on:

    {"api_key": "...", "skus": [...], "pricelist_id": 4}
    {"api_key": "...", "skus": [...], "pricelist_id": None}

pricelist_id is None when the base sale price itself changed
(list_price / lst_price), which affects every pricelist derived from it.

Mirrors the stock webhook in stock_update.py: URL and key come from
ir.config_parameter, the POST happens after commit on a daemon thread,
and every failure is swallowed. This module also owns the live order sync
(confirm_order_by_id and friends), so a webhook must never be able to
raise into a price write.
"""

import logging
import threading

import requests

from odoo import api, models
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)

# Written by the middleware on redeploy. Absent until then, which is not
# an error -- we simply stay quiet.
PRICE_WEBHOOK_PARAM = 'webhook_price_update'
API_KEY_PARAM = 'webhook_api_key'

# Key under which pending payloads accumulate in cr.postcommit.data.
PENDING_KEY = 'chimkins_price_sync'

REQUEST_TIMEOUT = 10


def _post_price_webhook(url, payload):
    """POST one payload. Runs on a daemon thread, touches no cursor."""
    try:
        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
            headers={'Content-Type': 'application/json'},
        )
        if response.status_code == 403:
            _logger.error("Price webhook rejected (403): check %s", API_KEY_PARAM)
        elif response.status_code not in (200, 201, 202):
            _logger.warning(
                "Price webhook failed: %s %s",
                response.status_code, response.text[:200],
            )
        else:
            _logger.info(
                "Price webhook sent: pricelist=%s, %s",
                payload.get('pricelist_id'),
                "full catalogue" if 'skus' not in payload
                else "%d sku(s)" % len(payload['skus']),
            )
    except Exception as err:
        _logger.warning("Price webhook error: %s", err)


def _fire_price_sync(pending):
    """Hand each accumulated payload to a thread, after commit.

    This must never raise. Odoo's Callbacks.run() calls each callback
    unguarded, so an exception here would propagate into the commit path
    and silently drop the callbacks still queued behind it -- including
    the stock webhook this module registers in the same transaction.
    """
    try:
        for pricelist_id, entry in pending['entries'].items():
            payload = {
                'api_key': pending['api_key'],
                'pricelist_id': pricelist_id,
            }
            if not entry['full_sync']:
                payload['skus'] = sorted(entry['skus'])
            thread = threading.Thread(
                target=_post_price_webhook,
                args=(pending['url'], payload),
            )
            thread.daemon = True
            thread.start()
    except Exception as err:
        _logger.error("Price webhook dispatch error: %s", err)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # ------------------------------------------------------------------
    # Dispatch, shared by every trigger below
    # ------------------------------------------------------------------

    @api.model
    def _price_precision(self):
        return self.env['decimal.precision'].precision_get('Product Price')

    @api.model
    def _price_differs(self, before, after):
        """True when two prices differ at Product Price precision.

        A missing side counts as a change: the product either entered or
        left the rule's coverage.
        """
        if before is None or after is None:
            return before is not after
        return float_compare(
            before, after, precision_digits=self._price_precision()
        ) != 0

    @api.model
    def _schedule_price_sync(self, skus=(), pricelist_id=None, full_sync=False):
        """Queue a price-change webhook, coalesced to one POST per commit.

        Config is read now, in the caller's transaction, so the sending
        thread never touches a cursor. Repeat calls within the same
        transaction merge per pricelist, so a bulk edit sends one POST
        carrying many SKUs rather than one per write.

        The accumulator lives in cr.postcommit.data, which Odoo clears on
        both commit and rollback, so it cannot leak into the next
        transaction on a pooled cursor.
        """
        try:
            icp = self.env['ir.config_parameter'].sudo()
            url = icp.get_param(PRICE_WEBHOOK_PARAM, default='')
            if not url:
                # Middleware not redeployed yet. Staying silent on purpose:
                # logging here would fire on every price edit.
                return

            skus = {sku for sku in (skus or ()) if sku}
            if not skus and not full_sync:
                return

            data = self.env.cr.postcommit.data
            pending = data.get(PENDING_KEY)
            if pending is None:
                pending = data[PENDING_KEY] = {
                    'entries': {},
                    'url': url,
                    'api_key': icp.get_param(API_KEY_PARAM, default=''),
                }
                self.env.cr.postcommit.add(lambda: _fire_price_sync(pending))

            entry = pending['entries'].setdefault(
                pricelist_id, {'skus': set(), 'full_sync': False}
            )
            entry['skus'] |= skus
            entry['full_sync'] = entry['full_sync'] or full_sync

        except Exception as err:
            # Never let webhook plumbing break a price write.
            _logger.error("Price webhook scheduling error: %s", err)

    # ------------------------------------------------------------------
    # Trigger: template sale price
    # ------------------------------------------------------------------

    def write(self, vals):
        if 'list_price' not in vals:
            return super().write(vals)

        before = {tmpl.id: tmpl.list_price for tmpl in self}
        result = super().write(vals)
        changed = self.filtered(
            lambda t: self._price_differs(before.get(t.id), t.list_price)
        )
        if changed:
            self._schedule_price_sync(
                changed.mapped('product_variant_ids.default_code'),
                pricelist_id=None,
            )
        return result


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def write(self, vals):
        # price_extra is computed and readonly in Odoo 16, so it will not
        # normally appear in vals; watched anyway because writing lst_price
        # is supported here and the field is writable in later versions.
        if 'lst_price' not in vals and 'price_extra' not in vals:
            return super().write(vals)

        before = {prod.id: prod.lst_price for prod in self}
        result = super().write(vals)
        Template = self.env['product.template']
        changed = self.filtered(
            lambda p: Template._price_differs(before.get(p.id), p.lst_price)
        )
        if changed:
            Template._schedule_price_sync(
                changed.mapped('default_code'), pricelist_id=None
            )
        return result


class ProductPricelistItem(models.Model):
    _inherit = 'product.pricelist.item'

    # ------------------------------------------------------------------
    # Coverage resolution
    # ------------------------------------------------------------------

    def _governed(self):
        """Which products each rule governs, as [(pricelist, products)].

        A global rule covers the whole catalogue. That is affordable here
        -- the full catalogue prices in well under a second -- so it is
        enumerated rather than degraded to a full-sync request, which
        would make the middleware walk every WooCommerce product.
        """
        Product = self.env['product.product'].sudo()
        governed = []
        for item in self:
            pricelist = item.pricelist_id
            if not pricelist:
                continue
            applied_on = item.applied_on
            if applied_on == '0_product_variant':
                products = item.product_id
            elif applied_on == '1_product':
                products = item.product_tmpl_id.product_variant_ids
            elif applied_on == '2_product_category':
                # sudo: record rules must not silently drop products from
                # the notification just because the editor cannot see them.
                products = Product.search(
                    [('categ_id', 'child_of', item.categ_id.id)]
                ) if item.categ_id else Product.browse()
            else:
                # '3_global', or anything a later version adds.
                products = Product.search([('default_code', '!=', False)])
            if products:
                governed.append((pricelist, products))
        return governed

    def _price_snapshot(self, governed):
        """{(pricelist_id, product_id): price} across the governed sets."""
        snapshot = {}
        for pricelist, products in governed:
            try:
                prices = pricelist._get_products_price(products, 1.0)
            except Exception as err:
                _logger.warning("Price snapshot failed for pricelist %s: %s",
                                pricelist.id, err)
                continue
            for product in products:
                snapshot[(pricelist.id, product.id)] = prices.get(product.id)
        return snapshot

    def _notify_price_moves(self, before, after, products_by_id):
        """Compare two snapshots and notify only what actually moved."""
        Template = self.env['product.template']
        moved = {}
        for key in set(before) | set(after):
            if Template._price_differs(before.get(key), after.get(key)):
                pricelist_id, product_id = key
                sku = products_by_id.get(product_id)
                if sku:
                    moved.setdefault(pricelist_id, set()).add(sku)
        for pricelist_id, skus in moved.items():
            Template._schedule_price_sync(skus, pricelist_id=pricelist_id)

    def _sku_map(self, *governed_sets):
        """{product_id: default_code} across every governed product."""
        mapping = {}
        for governed in governed_sets:
            for _pricelist, products in governed:
                for product in products:
                    if product.default_code:
                        mapping[product.id] = product.default_code
        return mapping

    # ------------------------------------------------------------------
    # Triggers
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        items = super().create(vals_list)
        # No before-snapshot is possible: the rule did not exist yet, and
        # reconstructing its coverage from vals would duplicate the
        # resolution logic. Creating a rule is a deliberate price change,
        # so every governed SKU is notified.
        governed = items._governed()
        sku_map = items._sku_map(governed)
        Template = self.env['product.template']
        by_pricelist = {}
        for pricelist, products in governed:
            for product in products:
                sku = sku_map.get(product.id)
                if sku:
                    by_pricelist.setdefault(pricelist.id, set()).add(sku)
        for pricelist_id, skus in by_pricelist.items():
            Template._schedule_price_sync(skus, pricelist_id=pricelist_id)
        return items

    def write(self, vals):
        governed_before = self._governed()
        before = self._price_snapshot(governed_before)

        result = super().write(vals)

        # Re-resolve: a write can repoint a rule at a different product,
        # category or pricelist, which moves prices on both sides.
        governed_after = self._governed()
        after = self._price_snapshot(governed_after)

        self._notify_price_moves(
            before, after, self._sku_map(governed_before, governed_after)
        )
        return result

    def unlink(self):
        governed = self._governed()
        sku_map = self._sku_map(governed)
        before = self._price_snapshot(governed)

        result = super().unlink()

        # The rules are gone but the products remain, so the same sets can
        # be repriced to see what the deletion actually moved.
        after = self._price_snapshot(governed)
        self._notify_price_moves(before, after, sku_map)
        return result
