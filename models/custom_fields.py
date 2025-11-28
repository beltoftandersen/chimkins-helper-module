# models/custom_fields.py
from odoo import models, fields

class AccountPaymentRegister(models.TransientModel):
    _inherit = "account.payment.register"

    custom_payment_ref = fields.Char(string="Payment Ref.")

    # called by action_create_payments(); always returns the recordset
    def _create_payments(self):
        payments = super()._create_payments()
        if self.custom_payment_ref and payments:
            payments.write({"custom_payment_ref": self.custom_payment_ref})
        return payments


class AccountPayment(models.Model):
    _inherit = "account.payment"

    custom_payment_ref = fields.Char(string="Payment Ref.")
