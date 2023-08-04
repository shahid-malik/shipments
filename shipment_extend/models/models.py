# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from datetime import datetime, time
from dateutil.relativedelta import relativedelta

from markupsafe import escape, Markup
from pytz import timezone, UTC
from werkzeug.urls import url_encode

from odoo import api, fields, models, _
from odoo.osv import expression
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT, format_amount, format_date, formatLang, get_lang, groupby
from odoo.tools.float_utils import float_compare, float_is_zero, float_round
from odoo.exceptions import UserError, ValidationError

from odoo.fields import Command


class Purchase(models.Model):
    _inherit = 'purchase.order'

    cs_shipment_count = fields.Integer(compute='compute_cs_shipment_count')

    def compute_cs_shipment_count(self):
        for rec in self:
            rec.cs_shipment_count = self.env['purchase.shipment'].search_count([('purchase_id', '=', rec.id)]) or False

    def action_open_shipment(self):
        self.ensure_one()
        return {
            'name': _('Shipment'),
            'view_mode': 'tree,form',
            'res_model': 'purchase.shipment',
            'domain': [('purchase_id', '=', self.id)],
            'view_id': False,
            'type': 'ir.actions.act_window',
            'context': {'default_purchase_id': self.id, 'default_order_line': self.order_line.ids}
        }


class PurchaseLine(models.Model):
    _inherit = 'purchase.order.line'

    cs_shipment_id = fields.Many2one('purchase.shipment', string='shipment')
    order_id = fields.Many2one('purchase.order', string='Order Reference', index=True, required=False,
                               ondelete='cascade')

    def _convert_to_tax_base_line_dict_custom(self):
        """ Convert the current record to a dictionary in order to use the generic taxes computation method
        defined on account.tax.

        :return: A python dictionary.
        """
        self.ensure_one()
        return self.env['account.tax']._convert_to_tax_base_line_dict(
            self,
            partner=self.cs_shipment_id.partner_id,
            currency=self.cs_shipment_id.currency_id,
            product=self.product_id,
            taxes=self.taxes_id,
            price_unit=self.price_unit,
            quantity=self.product_qty,
            price_subtotal=self.price_subtotal,
        )


class AccountMOve(models.Model):
    _inherit = 'account.move'

    cs_shipment_id = fields.Many2one('purchase.shipment', string='shipment')


class Shipment_extend(models.Model):
    _name = 'purchase.shipment'
    _description = 'purchase.shipment'
    _rec_name = 'name'

    state = fields.Selection([('new', 'New'), ('confirm', 'Confirm')], string="state", default="new")

    name = fields.Char(string="Name", default='New')
    date = fields.Datetime(string="Arrival Date")
    boxes = fields.Integer(string='Number of boxes')

    # purchase_ids = fields.One2many('purchase.order', 'cs_shipment_id', string='Purchase Order')
    purchase_id = fields.Many2one('purchase.order', string='Purchase Order')
    currency_id = fields.Many2one(related="purchase_id.currency_id")
    company_id = fields.Many2one(related="purchase_id.company_id")
    tax_country_id = fields.Many2one(related="purchase_id.tax_country_id")
    partner_id = fields.Many2one(related="purchase_id.partner_id")

    is_shipment_charges = fields.Boolean(string="Is Shipment Charges")
    shipment_charges = fields.Integer(string="Shipment Charges")
    shipment_tracking = fields.Char(string="Shipment Tracking")

    # order_line = fields.One2many('purchase.order.line', 'cs_shipment_id', string='Line Ids',)
    order_line = fields.One2many('purchase.order.line', 'cs_shipment_id', string='Line Ids', )
    amount_untaxed = fields.Monetary(string='Untaxed Amount', store=True, readonly=True, compute='_amount_all',
                                     tracking=True)
    tax_totals = fields.Binary(compute='_compute_tax_totals', exportable=False)
    amount_tax = fields.Monetary(string='Taxes', store=True, readonly=True, compute='_amount_all')
    amount_total = fields.Monetary(string='Total', readonly=True, compute='_amount_all')
    move_id = fields.Many2one('account.move', string='Account move', default=False)
    load_lines = fields.Boolean()

    invoice_count = fields.Integer(compute='compute_invoice_count')

    def action_confirm(self):
        self.state = 'confirm'

    def compute_invoice_count(self):
        for rec in self:
            rec.invoice_count = self.env['account.move'].search_count([('cs_shipment_id', '=', rec.id)]) or False

    @api.depends('order_line.taxes_id', 'order_line.price_subtotal', 'amount_total', 'amount_untaxed')
    def _compute_tax_totals(self):
        for order in self:
            order_lines = order.order_line.filtered(lambda x: not x.display_type)
            order.tax_totals = self.env['account.tax']._prepare_tax_totals(
                [x._convert_to_tax_base_line_dict_custom() for x in order_lines],
                order.currency_id or order.company_id.currency_id,
            )

    @api.model
    def create(self, vals):
        vals['name'] = self.env['ir.sequence'].next_by_code('po.shipment')
        res = super().create(vals)
        # for rec in res:
        #     new_lines = rec.order_line.copy()
        #     new_lines.order_id = False
        #     new_lines.cs_shipment_id = rec.id
        #     rec.order_line = new_lines
        return res

    def action_create_invoice(self):
        product = self.env.ref('shipment_extend.shipment_product')
        invoice_vals = self.purchase_id._prepare_invoice()
        sequence = 10
        for line in self.order_line:
            line_vals = {
                'display_type': line.display_type or 'product',
                'name': '%s: %s' % (line.cs_shipment_id.name, line.name),
                'product_id': line.product_id.id,
                'product_uom_id': line.product_uom.id,
                'quantity': line.product_qty,
                'price_unit': line.price_unit,
                'tax_ids': [(6, 0, line.taxes_id.ids)],
                'purchase_line_id': line.id,
            }
            line_vals.update({'sequence': sequence})
            invoice_vals['invoice_line_ids'].append((0, 0, line_vals))
            sequence += 1
        if self.is_shipment_charges:
            invoice_vals['invoice_line_ids'].append((0, 0, {
                'product_id': product.id,
                'sequence': sequence,
                'price_unit': self.shipment_charges,
                'quantity': 1.0,
                'name': 'Shipment',
            }))

        if invoice_vals:
            moves = self.env['account.move'].create(invoice_vals)
            moves.cs_shipment_id = self.id
            return self.purchase_id.action_view_invoice(moves)

    def action_view_invoice(self):
        return self.purchase_id.action_view_invoice(self)

    @api.depends('order_line.price_total', 'shipment_charges', 'amount_tax')
    def _amount_all(self):
        for order in self:
            order_lines = order.order_line.filtered(lambda x: not x.display_type)

            if order.company_id.tax_calculation_rounding_method == 'round_globally':
                tax_results = self.env['account.tax']._compute_taxes([
                    line._convert_to_tax_base_line_dict_custom()
                    for line in order_lines
                ])
                totals = tax_results['totals']
                amount_untaxed = totals.get(order.currency_id, {}).get('amount_untaxed', 0.0)
                amount_tax = totals.get(order.currency_id, {}).get('amount_tax', 0.0)
            else:

                amount_untaxed = sum(order_lines.mapped('price_subtotal'))
                amount_tax = sum([((line.price_subtotal / 100) * line.taxes_id.amount) for line in order_lines])

                order.amount_untaxed = amount_untaxed
                order.amount_tax = amount_tax
                order.amount_total = order.amount_untaxed + order.amount_tax + order.shipment_charges

        @api.model
        def default_get(self, fields):
            defaults = super().default_get(fields)
            if defaults.get('order_line'):
                ids = []
                for line in defaults.get('order_line'):
                    for id in line[2]:
                        new_line = self.env['purchase.order.line'].browse(id).copy()
                        new_line.order_id = False
                        ids.append(new_line.id)
                defaults['order_line'] = [Command.set(ids)]
            return defaults

        @api.onchange('is_shipment_charges')
        def on_is_shipment_charges(self):
            if not self.is_shipment_charges:
                self.shipment_charges = 0
