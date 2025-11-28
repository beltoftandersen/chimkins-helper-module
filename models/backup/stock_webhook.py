# /models/stock_webhook.py

from odoo import models
import logging
import requests

_logger = logging.getLogger(__name__)

class StockQuant(models.Model):
    _inherit = "stock.quant"

    def write(self, vals):
        """Override write to detect manual stock updates."""
        if "inventory_quantity" in vals and self.env.context.get("validate_inventory"):
            _logger.debug("Manual stock update detected in `write` method.")
            for quant in self:
                self._send_stock_update_webhook(quant)
        return super(StockQuant, self).write(vals)

    def _send_stock_update_webhook(self, quant):
        """Send a webhook when stock is updated manually."""
        _logger.debug(f"Preparing webhook for stock update: Product {quant.product_id.display_name}")

        api_key = self.env["ir.config_parameter"].sudo().get_param("webhook_api_key", "")
        odoo_db = self.env.cr.dbname
        odoo_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        webhook_url = self.env["ir.config_parameter"].sudo().get_param("webhook_stock_update", "")

        if not webhook_url:
            _logger.warning("Webhook URL not configured. Skipping webhook.")
            return

        quantity_config = self.env["ir.config_parameter"].sudo().get_param("webhook_quantity", "on-hand")
        if quantity_config == "on-hand":
            custom_quantity = quant.product_id.qty_available
        elif quantity_config == "forecast":
            custom_quantity = quant.product_id.virtual_available
        elif quantity_config == "available":
            custom_quantity = quant.product_id.qty_available - quant.product_id.outgoing_qty
        else:
            _logger.warning(f"Invalid webhook_quantity configuration: {quantity_config}. Skipping webhook.")
            return

        payload = {
            "api_key": api_key,
            "odoo_db": odoo_db,
            "odoo_url": odoo_url,
            "operation": "manual",
            "products": [
                {
                    "product_sku": quant.product_id.default_code,
                    "custom_quantity": custom_quantity,
                }
            ],
        }

        _logger.debug(f"Sending webhook with payload: {payload}")
        try:
            response = requests.post(webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
            response.raise_for_status()
            _logger.info(f"Webhook successfully sent to {webhook_url} with payload: {payload}")
        except requests.exceptions.RequestException as e:
            _logger.error(f"Failed to send webhook: {e}")
