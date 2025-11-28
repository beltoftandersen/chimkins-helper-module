# models/manufacturing_webhook.py
# -*- coding: utf-8 -*-
import json
import logging
import requests

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MrpProduction(models.Model):
    """
    Extends mrp.production to send a webhook after a manufacturing order
    is 'Done'. Only collects the FINISHED products.
    """
    _inherit = "mrp.production"

    def button_mark_done(self):
        """Override the 'Mark as Done' button to send a 'build' webhook."""
        res = super(MrpProduction, self).button_mark_done()
        for mo in self:
            if mo.state == "done":
                _logger.info(
                    f"[MRP] Manufacturing Order {mo.name} (ID={mo.id}) is done. "
                    "Sending build webhook for finished products..."
                )
                try:
                    mo._send_manufacturing_update_webhook(operation="build")
                except UserError as e:
                    # Log the error but don’t block the MO from completing
                    _logger.error(
                        f"[MRP] Webhook for MO {mo.name} not sent: {str(e)}"
                    )
        return res

    def _send_manufacturing_update_webhook(self, operation):
        """
        Sends a POST request to 'webhook_stock_update' with a JSON payload
        containing MO and finished-product info.
        """
        webhook_url = self.env["ir.config_parameter"].sudo().get_param("webhook_stock_update", default="")
        if not webhook_url:
            _logger.warning("[MRP] No webhook URL configured. (build) Webhook not sent.")
            return

        try:
            payload = self._prepare_manufacturing_webhook_payload(operation)
        except UserError as e:
            _logger.error(f"[MRP] Payload not prepared (build): {e}")
            return

        headers = {"Content-Type": "application/json"}
        try:
            response = requests.post(webhook_url, data=json.dumps(payload), headers=headers, timeout=10)
            response.raise_for_status()
            _logger.info(
                f"[MRP] Successfully sent BUILD webhook to {webhook_url} "
                f"with payload: {json.dumps(payload)}"
            )
            _logger.debug(f"[MRP] Build Webhook Response: {response.text}")
        except requests.exceptions.RequestException as e:
            _logger.error(f"[MRP] Failed to send build webhook to {webhook_url}: {str(e)}")
            # raise UserError(...) if you prefer blocking, or just log

    def _prepare_manufacturing_webhook_payload(self, operation):
        """
        Prepares a JSON payload of the finished products for the MO.
        This only collects FINISHED product lines (move_finished_ids).
        """
        api_key = self.env["ir.config_parameter"].sudo().get_param("webhook_api_key", default="")
        if not api_key:
            _logger.warning("[MRP] No global API key found for MO webhook. Webhook not sent.")
            raise UserError(_("Webhook API key is not configured."))

        odoo_db = self.env.cr.dbname
        odoo_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", default="")

        payload = {
            "api_key": api_key,
            "odoo_db": odoo_db,
            "odoo_url": odoo_url,
            "operation": operation,  # "build"
            "products": [],
        }

        quantity_config = self.env["ir.config_parameter"].sudo().get_param("webhook_quantity", default="")

        # Collect only finished products
        for move_finished in self.move_finished_ids.filtered(lambda m: m.product_id.type == "product"):
            product = move_finished.product_id
            processed_quantity = move_finished.quantity_done

            if not product.default_code:
                _logger.warning(
                    f"[MRP] Finished Product '{product.name}' (ID={product.id}) has no default_code. Skipping."
                )
                continue

            if processed_quantity <= 0:
                _logger.info(
                    f"[MRP] Skipping finished product '{product.default_code}' "
                    f"because quantity_done is {processed_quantity}."
                )
                continue

            custom_quantity = self._get_quantity_by_config(product, quantity_config)

            _logger.debug(
                f"[MRP] Building payload for Product {product.default_code} "
                f"(ID={product.id}), custom_quantity={custom_quantity}"
            )

            payload["products"].append({
                "product_sku": product.default_code,
                "custom_quantity": custom_quantity,
            })

        if not payload["products"]:
            _logger.warning(f"[MRP] No finished products found in MO {self.name}. Webhook won't be sent.")
            raise UserError(_("No storable products to send in MO webhook."))

        return payload

    def _get_quantity_by_config(self, product, quantity_config):
        """Helper to decide which quantity we send (on-hand, forecast, or available)."""
        if quantity_config == "on-hand":
            return product.qty_available
        elif quantity_config == "forecast":
            return product.virtual_available
        elif quantity_config == "available":
            return product.qty_available - product.outgoing_qty
        else:
            _logger.warning(
                f"[MRP] Invalid webhook_quantity parameter: {quantity_config}. "
                f"Defaulting to on-hand for '{product.name}'."
            )
            return product.qty_available


class MrpUnbuild(models.Model):
    """
    Extends mrp.unbuild to send a webhook when an Unbuild operation is completed.
    Since mrp.unbuild does NOT have move_finished_ids in Odoo 16, we reference:
      - unbuild.product_id (finished product being removed from stock)
      - unbuild.product_qty (quantity to unbuild)
    """
    _inherit = "mrp.unbuild"

    def action_unbuild(self):
        """Override to send an 'unbuild' webhook after the unbuild is done."""
        res = super(MrpUnbuild, self).action_unbuild()
        for unbuild in self:
            if unbuild.state == "done":
                _logger.info(
                    f"[MRP] Unbuild Order {unbuild.name} (ID={unbuild.id}) is done. "
                    "Sending unbuild webhook for the finished product..."
                )
                try:
                    unbuild._send_unbuild_update_webhook(operation="unbuild")
                except UserError as e:
                    _logger.error(f"[MRP] Webhook for unbuild {unbuild.name} not sent: {str(e)}")
        return res

    def _send_unbuild_update_webhook(self, operation):
        """Sends a POST request to the same webhook with 'unbuild' as the operation."""
        webhook_url = self.env["ir.config_parameter"].sudo().get_param("webhook_stock_update", default="")
        if not webhook_url:
            _logger.warning("[MRP] No webhook URL configured for unbuild. Webhook not sent.")
            return

        try:
            payload = self._prepare_unbuild_webhook_payload(operation)
        except UserError as e:
            _logger.error(f"[MRP] Unbuild Payload not prepared: {e}")
            return

        headers = {"Content-Type": "application/json"}
        try:
            response = requests.post(webhook_url, data=json.dumps(payload), headers=headers, timeout=10)
            response.raise_for_status()
            _logger.info(
                f"[MRP] Successfully sent UNBUILD webhook to {webhook_url} "
                f"with payload: {json.dumps(payload)}"
            )
            _logger.debug(f"[MRP] Unbuild Webhook Response: {response.text}")
        except requests.exceptions.RequestException as e:
            _logger.error(f"[MRP] Failed to send unbuild webhook: {str(e)}")

    def _prepare_unbuild_webhook_payload(self, operation):
        """
        mrp.unbuild does NOT store finished moves in a line field. 
        Instead, it has:
          - product_id (the finished product being unbuilt)
          - product_qty (the quantity to remove from stock)
        We'll include only that single product in the webhook.
        """
        api_key = self.env["ir.config_parameter"].sudo().get_param("webhook_api_key", default="")
        if not api_key:
            _logger.warning("[MRP] No global API key for unbuild. Webhook not sent.")
            raise UserError(_("Webhook API key is not configured."))

        odoo_db = self.env.cr.dbname
        odoo_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", default="")

        payload = {
            "api_key": api_key,
            "odoo_db": odoo_db,
            "odoo_url": odoo_url,
            "operation": operation,  # "unbuild"
            "products": [],
        }

        quantity_config = self.env["ir.config_parameter"].sudo().get_param("webhook_quantity", default="")

        product = self.product_id
        processed_quantity = self.product_qty  # The quantity being unbuilt

        # Make sure there's a valid product code
        if not product or not product.default_code:
            _logger.warning(
                f"[MRP] Unbuild: Product is missing or has no default_code. "
                f"Name={getattr(product, 'name', 'N/A')}, ID={getattr(product, 'id', 'N/A')}"
            )
            raise UserError(_("No default_code on the unbuilt product."))

        # If quantity is zero or negative, skip
        if processed_quantity <= 0:
            _logger.info(
                f"[MRP] Unbuild: Skipping {product.default_code} because quantity is {processed_quantity}."
            )
            raise UserError(_("Nothing to unbuild."))

        custom_quantity = self._get_quantity_by_config(product, quantity_config)

        _logger.debug(
            f"[MRP] Building unbuild data for Product {product.default_code} "
            f"(ID={product.id}), unbuild_qty={processed_quantity}, custom_quantity={custom_quantity}"
        )

        payload["products"].append({
            "product_sku": product.default_code,
            "custom_quantity": custom_quantity,
        })

        return payload

    def _get_quantity_by_config(self, product, quantity_config):
        """Same logic as in MrpProduction to keep it consistent."""
        if quantity_config == "on-hand":
            return product.qty_available
        elif quantity_config == "forecast":
            return product.virtual_available
        elif quantity_config == "available":
            return product.qty_available - product.outgoing_qty
        else:
            _logger.warning(
                f"[MRP] Invalid webhook_quantity parameter: {quantity_config}. "
                f"Defaulting to on-hand for '{product.name}'."
            )
            return product.qty_available
