# /models/purchase_order.py

import json
import logging
import requests
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class StockPicking(models.Model):
    _inherit = "stock.picking"

    def button_validate(self):
        res = super(StockPicking, self).button_validate()
        _logger.info(f"Validating pickings: {self.ids}")
        for picking in self:
            if picking.state == "done":
                if picking.picking_type_id.code == "incoming":
                    _logger.debug(f"Picking {picking.name} (ID={picking.id}) is a completed incoming shipment. Preparing webhook...")
                    try:
                        picking._send_stock_update_webhook(operation="purchase")
                    except UserError as e:
                        _logger.error(f"Webhook for incoming picking {picking.name} not sent: {str(e)}")

                elif picking.picking_type_id.code == "outgoing" and any(picking.move_ids.mapped("origin_returned_move_id")):
                    _logger.debug(f"Return {picking.name} (ID={picking.id}) is completed. Preparing webhook...")
                    try:
                        picking._send_stock_update_webhook(operation="return")
                    except UserError as e:
                        _logger.error(f"Webhook for return {picking.name} not sent: {str(e)}")

        return res


    def _send_stock_update_webhook(self, operation):
        webhook_url = self.env["ir.config_parameter"].sudo().get_param("webhook_stock_update", default="")
        if not webhook_url:
            _logger.warning("No webhook URL configured. Webhook not sent.")
            return

        try:
            payload = self._prepare_stock_webhook_payload(operation)
        except UserError as e:
            _logger.error(f"Webhook not prepared: {e}")
            raise UserError(f"Webhook not sent: {e}")

        headers = {"Content-Type": "application/json"}
        try:
            response = requests.post(webhook_url, data=json.dumps(payload), headers=headers, timeout=10)
            response.raise_for_status()
            _logger.info(f"Successfully sent stock update webhook to {webhook_url} with payload: {json.dumps(payload)}")
            _logger.debug(f"Webhook Response: {response.text}")
        except requests.exceptions.RequestException as e:
            _logger.error(f"Failed to send webhook to {webhook_url}: {str(e)}")
            raise UserError(f"Failed to send webhook: {e}")

    def _prepare_stock_webhook_payload(self, operation):
        api_key = self.env["ir.config_parameter"].sudo().get_param("webhook_api_key", default="")
        if not api_key:
            _logger.warning("No global API key found for webhook. Webhook not sent.")
            raise UserError(_("Webhook API key is not configured."))

        odoo_db = self.env.cr.dbname
        odoo_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", default="")

        payload = {
            "api_key": api_key,
            "odoo_db": odoo_db,
            "odoo_url": odoo_url,
            "operation": operation,
            "products": [],
        }

        quantity_config = self.env["ir.config_parameter"].sudo().get_param("webhook_quantity", default="")

        for move in self.move_ids.filtered(lambda m: m.product_id.type == "product"):
            product = move.product_id
            processed_quantity = move.quantity_done 

            if not product.default_code:
                _logger.warning(f"Product '{product.name}' (ID: {product.id}) has no default_code set. Skipping.")
                continue

            if processed_quantity <= 0:
                _logger.info(f"Skipping product '{product.default_code}' because quantity processed is {processed_quantity}.")
                continue

            if quantity_config == "on-hand":
                custom_quantity = product.qty_available
            elif quantity_config == "forecast":
                custom_quantity = product.virtual_available
            elif quantity_config == "available":
                custom_quantity = product.qty_available - product.outgoing_qty
            else:
                _logger.warning(f"Invalid webhook_quantity parameter: {quantity_config}. Skipping product '{product.name}'.")
                continue

            _logger.debug(f"Preparing webhook for Product ID: {product.id}, SKU: {product.default_code}, Custom Quantity: {custom_quantity}")

            payload["products"].append(
                {
                    "product_sku": product.default_code,
                    "custom_quantity": custom_quantity,
                }
            )

        if not payload["products"]:
            _logger.warning(f"No storable products found in picking {self.name}. Webhook will not be sent.")
            raise UserError(_("No storable products to send in webhook."))

        return payload
    
