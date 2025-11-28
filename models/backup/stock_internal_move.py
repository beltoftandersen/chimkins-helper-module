# -*- coding: utf-8 -*-
# models/stock_manual_webhook.py

import json
import logging
import requests

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def button_validate(self):
        """
        Extend the standard validation to trigger a manual webhook
        when an internal picking involves the location 'Market'. This
        means if either the source (location_id) or destination (location_dest_id)
        has the name 'Market', then the webhook is triggered with operation='manual'.
        """
        res = super(StockPicking, self).button_validate()
        for picking in self:
            if picking.state == "done":
                if picking.picking_type_id.code == "internal":
                    # Check if either source or destination location has the name "Market"
                    if (picking.location_id.name == "Market" or 
                        picking.location_dest_id.name == "Market"):
                        _logger.debug(f"[Stock Manual] Picking {picking.name} involves 'Market'. Sending 'manual' webhook.")
                        try:
                            picking._send_manual_webhook()
                        except UserError as e:
                            _logger.error(f"[Stock Manual] Manual webhook for {picking.name} not sent: {e}")
        return res

    def _send_manual_webhook(self):
        """
        Prepares and sends a stock update webhook with the operation set to 'manual'.
        This method uses system parameters for the webhook URL and API key.
        """
        webhook_url = self.env["ir.config_parameter"].sudo().get_param("webhook_stock_update", default="")
        if not webhook_url:
            _logger.warning("[Stock Manual] No webhook URL configured. Webhook not sent.")
            return

        try:
            payload = self._prepare_manual_webhook_payload()
        except UserError as e:
            _logger.error(f"[Stock Manual] Webhook payload not prepared: {e}")
            raise UserError(f"[Stock Manual] Webhook not sent: {e}")

        headers = {"Content-Type": "application/json"}
        try:
            response = requests.post(webhook_url, data=json.dumps(payload), headers=headers, timeout=10)
            response.raise_for_status()
            _logger.info(f"[Stock Manual] Successfully sent manual webhook to {webhook_url} with payload: {json.dumps(payload)}")
            _logger.debug(f"[Stock Manual] Webhook Response: {response.text}")
        except requests.exceptions.RequestException as e:
            _logger.error(f"[Stock Manual] Failed to send manual webhook to {webhook_url}: {e}")
            raise UserError(f"[Stock Manual] Failed to send manual webhook: {e}")

    def _prepare_manual_webhook_payload(self):
        """
        Assemble the payload data for the manual webhook. The payload includes:
          - API key from system parameters (webhook_api_key)
          - The current Odoo database name
          - The base URL of Odoo
          - The operation, fixed to 'manual'
          - A list of products, each with the product SKU and the custom quantity
          
        The custom quantity is computed using the helper method _get_quantity_by_config,
        matching the logic in your manufacturing webhook.
        """
        api_key = self.env["ir.config_parameter"].sudo().get_param("webhook_api_key", default="")
        if not api_key:
            _logger.warning("[Stock Manual] No global API key found. Webhook not sent.")
            raise UserError(_("Webhook API key is not configured."))

        odoo_db = self.env.cr.dbname
        odoo_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", default="")

        payload = {
            "api_key": api_key,
            "odoo_db": odoo_db,
            "odoo_url": odoo_url,
            "operation": "manual",
            "products": [],
        }

        # Retrieve the quantity configuration parameter (e.g. "on-hand", "forecast", "available")
        quantity_config = self.env["ir.config_parameter"].sudo().get_param("webhook_quantity", default="")

        # Iterate over moves for storable products
        for move in self.move_ids.filtered(lambda m: m.product_id.type == "product"):
            product = move.product_id
            qty_done = move.quantity_done
            if not product.default_code:
                _logger.warning(f"[Stock Manual] Product '{product.name}' (ID={product.id}) is missing a default_code. Skipping.")
                continue
            if qty_done <= 0:
                _logger.info(f"[Stock Manual] Skipping product '{product.default_code}' because quantity_done is {qty_done}.")
                continue

            # Compute the custom quantity using the helper function:
            custom_quantity = self._get_quantity_by_config(product, quantity_config)

            payload["products"].append({
                "product_sku": product.default_code,
                "custom_quantity": custom_quantity,
            })

        if not payload["products"]:
            _logger.warning(f"[Stock Manual] No valid products found in picking {self.name}. Manual webhook will not be sent.")
            raise UserError(_("No storable products to send in manual webhook."))

        return payload

    def _get_quantity_by_config(self, product, quantity_config):
        qty_available = product.qty_available
        virtual_available = product.virtual_available
        outgoing_qty = product.outgoing_qty

        if quantity_config == "on-hand":
            result = qty_available
        elif quantity_config == "forecast":
            result = virtual_available
        elif quantity_config == "available":
            result = qty_available - outgoing_qty
        else:
            _logger.warning(
                f"[Stock Manual] Invalid webhook_quantity parameter: '{quantity_config}'. "
                f"Defaulting to on-hand for '{product.display_name}'."
            )
            result = qty_available

        _logger.info(
            f"[Stock QtyCalc] {product.default_code}: custom_quantity={result} "
            f"(qty_available={qty_available}, outgoing_qty={outgoing_qty}, forecast={virtual_available}, config='{quantity_config}')"
        )
        return result
