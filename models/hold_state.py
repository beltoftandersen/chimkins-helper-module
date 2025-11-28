# custom_addons/helper_module/models/hold_state.py

from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _action_done(self, *args, **kwargs):
        """Override _action_done to prevent automatic assignment of
        SO deliveries upon validating PO receipts. Then, after sending
        confirmation email, force-assign the deliveries only if the SO
        is fully paid (and has at least one invoice).
        """
        self._check_company()

        todo_moves = self.move_ids.filtered(
            lambda m: m.state in ['draft', 'waiting', 'partially_available', 'assigned', 'confirmed']
        )
        # If there's an owner set on the picking, propagate it to moves/lines.
        for picking in self:
            if picking.owner_id:
                picking.move_ids.write({'restrict_partner_id': picking.owner_id.id})
                picking.move_line_ids.write({'owner_id': picking.owner_id.id})

        cancel_backorder = kwargs.get('cancel_backorder', self.env.context.get('cancel_backorder'))
        todo_moves._action_done(cancel_backorder=cancel_backorder)

        self.write({'date_done': fields.Datetime.now(), 'priority': '0'})

        self._send_confirmation_email()

        if self.picking_type_id.code == 'incoming' and self.origin and self.origin.startswith("P"):
            _logger.info(
                "Picking %s is a Purchase Order receipt (%s). Running assign_deliveries_for_paid_so_self().",
                self.name, self.origin
            )
            self.assign_deliveries_for_paid_so_self()
        else:
            _logger.info(
                "Skipping assign_deliveries_for_paid_so_self() for Picking %s (%s). Not a PO receipt.",
                self.name, self.origin
            )

        return True


    def assign_deliveries_for_paid_so_self(self):
        _logger.info("Executing assign_deliveries_for_paid_so() for pickings: %s", self.ids)

        product_ids = self.move_line_ids.filtered(
            lambda ml: getattr(ml, 'quantity', getattr(ml, 'qty_done', 0)) > 0
        ).mapped('product_id').ids
        if not product_ids:
            _logger.info("No products were received in this picking. Exiting function.")
            return  

        _logger.info("Products received in this picking: %s", product_ids)

        sale_orders = self.env['sale.order'].search([
            ('order_line.product_id', 'in', product_ids),
            ('invoice_ids', '!=', False),
            ('invoice_status', '=', 'invoiced'),
            ('invoice_ids.payment_state', '=', 'paid'),
        ])

        if not sale_orders:
            _logger.info("No Sale Orders found that match the criteria (fully invoiced, fully paid, contains received products).")
            return

        _logger.info("Found %d Sale Orders that meet the criteria: %s", len(sale_orders), sale_orders.ids)

        for so in sale_orders:
            picking = self.env["stock.picking"].search([
                ("origin", "=", so.name),
                ("state", "in", ["waiting", "confirmed"])
            ], order="id asc", limit=1)

            if not picking:
                _logger.info("Sale Order %s has no waiting or confirmed pickings. Skipping.", so.id)
                continue

            # Check if stock is available but not yet reserved
            stock_available_products = picking.move_ids.filtered(
                lambda m: m.reserved_availability == 0 and m.product_id.qty_available >= m.product_uom_qty
            )

            # Check if ALL products in the picking have enough available stock
            missing_stock_products = picking.move_ids.filtered(
                lambda m: m.product_id.qty_available < m.product_uom_qty
            )

            if missing_stock_products:
                _logger.info(
                    "Skipping picking %s for Sale Order %s due to missing stock for: %s",
                    picking.id, so.id, missing_stock_products.mapped("product_id.name")
                )
                continue  # Skip assignment if any product is missing stock

            # If all products have enough stock but aren't reserved, force assign
            if stock_available_products:
                _logger.info(
                    "Stock available but not reserved for picking %s. Forcing reservation.",
                    picking.id
                )
                picking.action_assign()
                _logger.info("After action_assign(), picking %s is in state: '%s'", picking.id, picking.state)
