from odoo import models, fields


class IrCron(models.Model):
    _inherit = 'ir.cron'
    
    def _cron_sync_harvest_data(self):
        configs = self.env['harvest.config'].search([('active', '=', True)])
        for config in configs:
            config.sync_harvest_data()