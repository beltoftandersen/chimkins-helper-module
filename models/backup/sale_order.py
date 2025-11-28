# /models/sale_order.py

import json
import logging
import requests
from odoo import models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _prepare_webhook_payload(self):
        api_key = self.env['ir.config_parameter'].sudo().get_param('webhook_api_key', default='')
        if not api_key:
            _logger.warning("No global API key found for webhook. Webhook not sent.")
            raise UserError("Webhook API key is not configured.")

        odoo_db = self.env.cr.dbname
        odoo_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', default='')

        payload = {
            "api_key": api_key,
            "odoo_db": odoo_db,
            "odoo_url": odoo_url,
            "operation": "sale",
            "products": []
        }

        quantity = self.env['ir.config_parameter'].sudo().get_param('webhook_quantity', default='')

        for line in self.order_line:
            product = line.product_id

            if product.type != 'product':
                _logger.debug(f"Product '{product.name}' (ID: {product.id}) is not storable. Skipping.")
                continue

            if not product.default_code:
                _logger.warning(f"Product '{product.name}' (ID: {product.id}) has no default_code set. Skipping.")
                continue

            if quantity == 'on-hand':
                custom_quantity = product.qty_available
            elif quantity == 'forecast':
                custom_quantity = product.virtual_available
            elif quantity == 'available':
                custom_quantity = product.qty_available - product.outgoing_qty
            else:
                _logger.warning(f"Invalid webhook_quantity parameter: {quantity}. Skipping product '{product.name}' (ID: {product.id}).")
                continue

            _logger.debug(f"Preparing webhook for Product ID: {product.id}, SKU: {product.default_code}, Custom Quantity: {custom_quantity}")

            payload["products"].append({
                "product_sku": product.default_code,
                "custom_quantity": custom_quantity,
            })

        if not payload["products"]:
            _logger.warning(f"No storable products found in Sale Order {self.id}. Webhook will not be sent.")
            raise UserError("No storable products to send in webhook.")

        return payload

    def _send_webhook(self):
        webhook_url = self.env['ir.config_parameter'].sudo().get_param('webhook_stock_update', default='')
        if not webhook_url:
            _logger.warning("No global API key found for webhook. Webhook not sent.")
        try:
            payload = self._prepare_webhook_payload()
        except UserError as e:
            _logger.error(f"Webhook not prepared: {e}")
            raise UserError(f"Webhook not sent: {e}")

        headers = {'Content-Type': 'application/json'}

        try:
            response = requests.post(webhook_url, data=json.dumps(payload), headers=headers, timeout=10)
            response.raise_for_status()
            _logger.info(f"Successfully sent webhook to {webhook_url} with payload: {json.dumps(payload)}")
            _logger.debug(f"Webhook Response: {response.text}")
        except requests.exceptions.RequestException as e:
            _logger.error(f"Failed to send webhook to {webhook_url}: {e}")
            raise UserError(f"Failed to send webhook: {e}")

    def action_confirm(self):
        res = super(SaleOrder, self).action_confirm()
        try:
            self._send_webhook()
        except UserError as e:
            _logger.error(f"Webhook not sent during confirmation: {e}")
        return res

    def action_cancel(self):
        res = super(SaleOrder, self).action_cancel()
        try:
            self._send_webhook()
        except UserError as e:
            _logger.error(f"Webhook not sent during cancellation: {e}")
        return res
