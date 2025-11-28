# /models/fields.py

from odoo import models, fields, api
from odoo.tools import html_escape

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    custom_available_quantity = fields.Float(
        string="Custom Available Quantity",
        compute="_compute_custom_available_quantity",
        store=True,
        help="On-hand quantity minus reserved stock.",
        digits=(16, 0)
    )

    @api.depends('product_variant_ids', 'product_variant_ids.custom_available_quantity')
    def _compute_custom_available_quantity(self):
        for template in self:
            template.custom_available_quantity = sum(
                variant.custom_available_quantity for variant in template.product_variant_ids
            )


class ProductProduct(models.Model):
    _inherit = 'product.product'

    custom_available_quantity = fields.Float(
        string="Custom Available Quantity",
        compute="_compute_custom_available_quantity",
        store=True,
        help="On-hand quantity minus reserved stock.",
        digits=(16, 0)
    )

    @api.depends('qty_available', 'outgoing_qty')
    def _compute_custom_available_quantity(self):
        for product in self:
            reserved_qty = sum(self.env['stock.quant'].search([
                ('product_id', '=', product.id)
            ]).mapped('reserved_quantity'))
            product.custom_available_quantity = product.qty_available - product.outgoing_qty


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    woocommerce_order_id = fields.Char(
        string="WooCommerce Order ID",
        index=True, 
        copy=False
    )

    woocommerce_url = fields.Char(
        string="WooCommerce Store URL",
        help="Base URL of the WooCommerce store.",
        copy=False
    )

    woocommerce_order_link = fields.Html(
        string="WooCommerce Order Link",
        compute="_compute_woocommerce_order_link",
        store=False
    )

    _sql_constraints = [
        ('woocommerce_order_id_unique', 'unique(woocommerce_order_id)', 'WooCommerce Order ID must be unique.')
    ]

    @api.depends('woocommerce_order_id', 'woocommerce_url')
    def _compute_woocommerce_order_link(self):
        for order in self:
            if order.woocommerce_order_id:
                order_id_for_link = order.woocommerce_order_id[3:] 
                
                if order.woocommerce_url:
                    woo_base_url = f"{order.woocommerce_url}/wp-admin/post.php?post={order_id_for_link}&action=edit"
                    order.woocommerce_order_link = (
                        f'<a href="{html_escape(woo_base_url)}" target="_blank">'
                        f'{html_escape(order.woocommerce_order_id)}</a>'
                    )
                else:
                    order.woocommerce_order_link = f'<span>{html_escape(order.woocommerce_order_id)}</span>'
            else:
                order.woocommerce_order_link = ''


class AccountMove(models.Model):
    _inherit = "account.move"

    woocommerce_order_id = fields.Char(
        string="WooCommerce Order ID",
        help="The WooCommerce Order ID associated with this invoice.",
        index=True,
    )

    woocommerce_url = fields.Char(
        string="WooCommerce Store URL",
        help="Base URL of the WooCommerce store.",
        copy=False
    )

    woocommerce_refund_id = fields.Char(
        string="WooCommerce Refund ID", 
        index=True,
    )

    woocommerce_order_link = fields.Html(
        string="WooCommerce Order Link",
        compute="_compute_woocommerce_order_link",
        store=False
    )

    @api.depends('woocommerce_order_id', 'woocommerce_url')
    def _compute_woocommerce_order_link(self):
        for order in self:
            if order.woocommerce_order_id:
                order_id_for_link = order.woocommerce_order_id[3:] 
                
                if order.woocommerce_url:
                    woo_base_url = f"{order.woocommerce_url}/wp-admin/post.php?post={order_id_for_link}&action=edit"
                    order.woocommerce_order_link = (
                        f'<a href="{html_escape(woo_base_url)}" target="_blank">'
                        f'{html_escape(order.woocommerce_order_id)}</a>'
                    )
                else:
                    order.woocommerce_order_link = f'<span>{html_escape(order.woocommerce_order_id)}</span>'
            else:
                order.woocommerce_order_link = ''

class StockMove(models.Model):
    _inherit = 'stock.move'

    woocommerce_order_id = fields.Char(
        string="WooCommerce Order ID",
        index=True,
        copy=False,
    )