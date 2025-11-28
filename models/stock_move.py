from odoo import models, fields, api
import requests
from odoo.exceptions import UserError
import logging

logger = logging.getLogger(__name__)

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    woocommerce_order_id = fields.Char(
        string="WooCommerce Order ID",
        help="WooCommerce Order associated with this picking."
    )
    # Optional flag to mark that a webhook has been sent for this order
    woocommerce_webhook_sent = fields.Boolean(
        string="WooCommerce Webhook Sent",
        default=False,
        help="Indicates if a webhook has already been sent for this WooCommerce order."
    )

    @api.model
    def create(self, vals):
        picking = super(StockPicking, self).create(vals)
        if 'origin' in vals:
            sale_order = self.env['sale.order'].search([('name', '=', vals['origin'])], limit=1)
            if sale_order and sale_order.woocommerce_order_id:
                picking.woocommerce_order_id = sale_order.woocommerce_order_id
        if picking.woocommerce_order_id:
            picking.move_ids.write({'woocommerce_order_id': picking.woocommerce_order_id})
        return picking

    def button_validate(self):
        res = super(StockPicking, self).button_validate()
        for picking in self:
            # Only consider outgoing or direct pickings (adjust as needed)
            if picking.woocommerce_order_id and picking.picking_type_id.code in ['outgoing', 'direct']:
                picking._check_and_send_webhook()
        return res

    def _check_and_send_webhook(self):
        """Check if all pickings for this WooCommerce order are done, and if so, send a webhook."""
        # If webhook already sent, do nothing
        if self.woocommerce_webhook_sent:
            return

        # Get all pickings with the same WooCommerce order and of relevant type
        related_pickings = self.search([
            ('woocommerce_order_id', '=', self.woocommerce_order_id),
            ('picking_type_id.code', 'in', ['outgoing', 'direct']),
        ])
        # Check if all these pickings are done
        if all(p.state == 'done' for p in related_pickings):
            self._send_woocommerce_webhook()
            # Mark all related pickings (or the order) as webhook sent
            related_pickings.write({'woocommerce_webhook_sent': True})
        else:
            logger.info(f"Not all pickings for WooCommerce Order ID '{self.woocommerce_order_id}' are done.")

    def _send_woocommerce_webhook(self):
        """Send the webhook to update the WooCommerce order status."""
        api_key = self.env['ir.config_parameter'].sudo().get_param('webhook_api_key', default='')
        if not api_key:
            logger.warning("No global API key found for webhook. Webhook not sent.")
            raise UserError("Webhook API key is not configured.")

        url = self.env['ir.config_parameter'].sudo().get_param('webhook_change_status', default='')
        if not url:
            logger.warning("No return URL found for webhook. Webhook not sent.")
            return

        payload = {
            'woocommerce_order_id': self.woocommerce_order_id,
            'status': 'completed',
            'date_done': self.date_done.isoformat() if self.date_done else None,
            'api_key': api_key,
        }
        headers = {'Content-Type': 'application/json'}
        try:
            logger.info(f"Sending webhook for WooCommerce Order ID '{self.woocommerce_order_id}' with payload: {payload}")
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            logger.info(f"Webhook sent successfully for WooCommerce Order ID '{self.woocommerce_order_id}'.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send webhook for WooCommerce Order ID '{self.woocommerce_order_id}': {e}")
