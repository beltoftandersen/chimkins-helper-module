import requests
import json
import time
from odoo import models, fields, api
import logging
from odoo.tools import config

_logger = logging.getLogger(__name__)

class StockQuant(models.Model):
    _inherit = 'stock.quant'
    @api.model
    def _get_webhook_dedup_key(self, products, operation_type='default'):
        """Generate consistent deduplication key"""
        product_ids = ','.join(map(str, sorted(products.ids)))
        return f"webhook_{product_ids}_{operation_type}_{int(time.time())}"

    @api.model
    def _is_webhook_already_scheduled(self, products, operation_type='default'):
        """Check if webhook is already scheduled for these products"""
        if not hasattr(self.env.registry, '_webhook_scheduled'):
            self.env.registry._webhook_scheduled = {}
        
        current_time = int(time.time())
        old_keys = [k for k, v in self.env.registry._webhook_scheduled.items() 
                   if current_time - v > 5]
        for k in old_keys:
            del self.env.registry._webhook_scheduled[k]
        
        base_key = f"webhook_{','.join(map(str, sorted(products.ids)))}"
        existing_keys = [k for k in self.env.registry._webhook_scheduled.keys() 
                        if k.startswith(base_key)]
        
        return bool(existing_keys)

    @api.model
    def _mark_webhook_scheduled(self, products, operation_type='default'):
        """Mark webhook as scheduled for deduplication"""
        if not hasattr(self.env.registry, '_webhook_scheduled'):
            self.env.registry._webhook_scheduled = {}
        
        dedup_key = self._get_webhook_dedup_key(products, operation_type)
        self.env.registry._webhook_scheduled[dedup_key] = int(time.time())
        return dedup_key

    @api.model
    def _send_webhook_with_retry(self, webhook_url, payload, max_retries=3):
        """Send webhook with exponential backoff retry"""
        
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    webhook_url,
                    json=payload,
                    timeout=10,
                    headers={'Content-Type': 'application/json'}
                )
                
                if response.status_code in [200, 201, 202]:
                    _logger.info(f"Stock webhook sent successfully (attempt {attempt + 1}) for {len(payload['products'])} products")
                    return True
                else:
                    _logger.warning(f"Webhook attempt {attempt + 1} failed: {response.status_code} - {response.text}")
                    
            except requests.exceptions.Timeout:
                _logger.warning(f"Webhook attempt {attempt + 1} timeout")
            except requests.exceptions.RequestException as e:
                _logger.warning(f"Webhook attempt {attempt + 1} failed: {str(e)}")
            except Exception as e:
                _logger.error(f"Webhook attempt {attempt + 1} unexpected error: {str(e)}")
            
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
        
        _logger.error(f"Webhook failed after {max_retries} attempts")
        return False

    @api.model
    def _send_stock_webhook(self, products):
        """Send computed stock via webhook - optimized version"""
        if not products:
            _logger.info(f"Stock webhook skipped: products={bool(products)}")
            return

        try:
            api_key = self.env['ir.config_parameter'].sudo().get_param('webhook_api_key', default='')
            odoo_db = self.env.cr.dbname
            odoo_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', default='')
            webhook_url = self.env['ir.config_parameter'].sudo().get_param('webhook_stock_update', default='')

            if not webhook_url:
                return

            stock_data = []
            for product in products:
                on_hand = product.qty_available
                forecast = product.virtual_available
                available = product.qty_available - product.outgoing_qty

                stock_data.append({
                    'product_id': product.id,
                    'product_sku': product.default_code or '',
                    'product_name': product.name,
                    'on_hand': on_hand,
                    'forecast': forecast,
                    'available': available
                })

            payload = {
                'timestamp': fields.Datetime.now().isoformat(),
                'api_key': api_key,
                'odoo_db': odoo_db,
                'odoo_url': odoo_url,
                'operation': 'stock_update',
                'products': stock_data
            }

            self._send_webhook_async(webhook_url, payload)

        except Exception as e:
            _logger.error(f"Stock webhook preparation error: {str(e)}")

    @api.model
    def _send_webhook_async(self, webhook_url, payload):
        """Send webhook asynchronously to avoid blocking stock operations"""
        try:
            import threading

            def send_request():
                self._send_webhook_with_retry(webhook_url, payload)

            thread = threading.Thread(target=send_request)
            thread.daemon = True
            thread.start()

        except Exception as e:
            _logger.error(f"Failed to start webhook thread: {str(e)}")

class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        """Trigger webhook on move completion"""
        _logger.info(f"Stock move _action_done called for moves: {self.ids}")
        result = super()._action_done(cancel_backorder)

        affected_products = self.mapped('product_id').filtered(
            lambda p: p.type == 'product' and p.sale_ok
        )
        _logger.info(f"Stock webhook trigger from moves: {len(affected_products)} products affected")
        if affected_products:
            self._schedule_post_commit_webhook(affected_products, 'done')

        return result

    def _action_assign(self, force_qty=None, *args, **kwargs):
        """Trigger webhook when stock is reserved (SO confirmation)"""
        context_skip = self.env.context.get('skip_stock_webhook', False)
        affected_products = self.mapped('product_id').filtered(
            lambda p: p.type == 'product' and p.sale_ok
        )

        result = super()._action_assign()
        
        will_be_done = any(move.state == 'assigned' and move.picking_id.state == 'assigned' for move in self)
        
        if affected_products and not context_skip and not will_be_done:
            _logger.info(f"Stock webhook trigger from reservation: {len(affected_products)} products affected")
            self._schedule_post_commit_webhook(affected_products, 'assign')
        
        return result

    def _action_cancel(self):
        """Trigger webhook when moves are cancelled (unreserve stock)"""
        context_skip = self.env.context.get('skip_stock_webhook', False)
        affected_products = self.mapped('product_id')
        result = super()._action_cancel()
        
        if affected_products and not context_skip:
            _logger.info(f"Stock webhook trigger from cancellation: {len(affected_products)} products affected")
            self._schedule_post_commit_webhook(affected_products, 'cancel')
        
        return result

    def _schedule_post_commit_webhook(self, products, operation_type):
        """Schedule webhook to run after transaction commit with centralized deduplication"""
        if self.env['stock.quant']._is_webhook_already_scheduled(products, operation_type):
            _logger.info(f"Skipped duplicate webhook for operation {operation_type}")
            return

        def send_webhook():
            try:
                with self.env.registry.cursor() as new_cr:
                    new_env = api.Environment(new_cr, self.env.uid, self.env.context)
                    fresh_products = new_env['product.product'].browse(products.ids)
                    new_env['stock.quant']._send_stock_webhook(fresh_products)
            except Exception as e:
                _logger.error(f"Post-commit webhook error: {str(e)}")

        dedup_key = self.env['stock.quant']._mark_webhook_scheduled(products, operation_type)
        self.env.cr.postcommit.add(send_webhook)
        _logger.info(f"Scheduled webhook for operation {operation_type} with key {dedup_key}")


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        """Trigger webhook when SO is confirmed (stock reserved)"""
        result = super(SaleOrder, self.with_context(skip_stock_webhook=True)).action_confirm()

        affected_products = self.order_line.mapped('product_id').filtered(
            lambda p: p.type == 'product'
        )

        if affected_products:
            _logger.info(f"Stock webhook trigger from SO confirmation: {len(affected_products)} products affected")
            self._schedule_post_commit_webhook(affected_products, 'so_confirm')

        return result

    def action_cancel(self):
        """Trigger webhook when SO is cancelled (stock unreserved)"""
        affected_products = self.order_line.mapped('product_id').filtered(
            lambda p: p.type == 'product'
        )

        was_confirmed = any(order.state in ['sale', 'done'] for order in self)
        
        result = super(SaleOrder, self.with_context(skip_stock_webhook=True)).action_cancel()
        
        actually_cancelled = any(order.state == 'cancel' for order in self)

        if affected_products and was_confirmed and actually_cancelled:
            _logger.info(f"Stock webhook trigger from SO cancellation: {len(affected_products)} products affected")
            self._schedule_post_commit_webhook(affected_products, 'so_cancel')
        elif not actually_cancelled:
            _logger.info(f"SO cancellation webhook skipped - orders not actually cancelled")

        return result

    def _schedule_post_commit_webhook(self, products, operation_type):
        """Schedule webhook to run after transaction commit with centralized deduplication"""
        if self.env['stock.quant']._is_webhook_already_scheduled(products, operation_type):
            _logger.info(f"Skipped duplicate webhook for operation {operation_type}")
            return

        def send_webhook():
            try:
                with self.env.registry.cursor() as new_cr:
                    new_env = api.Environment(new_cr, self.env.uid, self.env.context)
                    fresh_products = new_env['product.product'].browse(products.ids)
                    new_env['stock.quant']._send_stock_webhook(fresh_products)
            except Exception as e:
                _logger.error(f"Post-commit webhook error: {str(e)}")

        dedup_key = self.env['stock.quant']._mark_webhook_scheduled(products, operation_type)
        self.env.cr.postcommit.add(send_webhook)
        _logger.info(f"Scheduled SO webhook for operation {operation_type} with key {dedup_key}")
