from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import json
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class HarvestConfig(models.Model):
    _name = 'harvest.config'
    _description = 'Harvest API Configuration'
    _rec_name = 'account_id'

    account_id = fields.Char('Harvest Account ID', required=True)
    access_token = fields.Char('Personal Access Token', required=True)
    api_url = fields.Char(
        'API Base URL', default='https://api.harvestapp.com/v2/', readonly=True)
    last_sync = fields.Datetime('Last Synchronization')
    sync_days_back = fields.Integer(
        'Days to Sync Back', default=30, help="Number of days to sync retroactively")
    sync_all_dates = fields.Boolean(
        string='Sync All Dates', default=False,
        help="If checked, syncs all time entries regardless of date (ignores 'Days to Sync Back')")
    active = fields.Boolean('Active', default=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, default=lambda self: self.env.company)

    # Sync level configuration
    sync_level = fields.Selection([
        ('my_time', 'My Time Entries Only'),
        ('all_time', 'All Time Entries'),
        ('full', 'Full Sync (Users, Projects, Time)')
    ], string='Sync Level', default='my_time', required=True,
        help="Choose what data to sync based on your API permissions")
    can_access_users = fields.Boolean(
        'Can Access Users', default=False, readonly=True)
    can_access_projects = fields.Boolean(
        'Can Access Projects', default=False, readonly=True)
    can_access_all_time = fields.Boolean(
        'Can Access All Time Entries', default=False, readonly=True)
    current_user_id = fields.Char('Current Harvest User ID', readonly=True)

    @api.constrains('active')
    def _check_single_active(self):
        if self.active:
            existing = self.search(
                [('active', '=', True), ('company_id', '=', self.company_id.id), ('id', '!=', self.id)])
            if existing:
                raise UserError(
                    _('Only one active Harvest configuration is allowed per company.'))

    def _get_headers(self):
        return {
            'Harvest-Account-ID': self.account_id,
            'Authorization': f'Bearer {self.access_token}',
            'User-Agent': 'Odoo Harvest Integration',
            'Content-Type': 'application/json'
        }

    def test_connection(self):
        try:
            response = requests.get(
                f'{self.api_url}company',
                headers=self._get_headers(),
                timeout=10
            )
            if response.status_code == 200:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Connection to Harvest API successful!'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise UserError(_('Connection failed: %s') % response.text)
        except Exception as e:
            raise UserError(_('Connection failed: %s') % str(e))

    def check_access_levels(self):
        """Check what API endpoints are accessible and update access level fields"""
        self.ensure_one()

        access_info = {
            'can_access_users': False,
            'can_access_projects': False,
            'can_access_all_time': False,
            'current_user_id': False
        }

        # Check access to current user (always available)
        try:
            response = requests.get(
                f'{self.api_url}users/me',
                headers=self._get_headers(),
                timeout=10
            )
            if response.status_code == 200:
                user_data = response.json()
                access_info['current_user_id'] = str(user_data.get('id'))
        except:
            pass

        # Check access to all users
        try:
            response = requests.get(
                f'{self.api_url}users',
                headers=self._get_headers(),
                params={'per_page': 1},
                timeout=10
            )
            if response.status_code == 200:
                access_info['can_access_users'] = True
        except:
            pass

        # Check access to projects
        try:
            response = requests.get(
                f'{self.api_url}projects',
                headers=self._get_headers(),
                params={'per_page': 1},
                timeout=10
            )
            if response.status_code == 200:
                access_info['can_access_projects'] = True
        except:
            pass

        # Check access to all time entries
        try:
            response = requests.get(
                f'{self.api_url}time_entries',
                headers=self._get_headers(),
                params={'per_page': 1},
                timeout=10
            )
            if response.status_code == 200:
                access_info['can_access_all_time'] = True
        except:
            pass

        # Update fields
        self.sudo().write(access_info)

        # Auto-adjust sync level based on permissions
        if not access_info['can_access_users'] and not access_info['can_access_projects']:
            self.sync_level = 'my_time'
        elif not access_info['can_access_users']:
            self.sync_level = 'all_time'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Access Levels Checked'),
                'message': _('API permissions have been verified and sync level adjusted.'),
                'type': 'info',
                'sticky': False,
            }
        }

    def sync_harvest_data(self):
        self.ensure_one()
        try:
            # Check access levels first if not already done
            if not self.current_user_id:
                self.check_access_levels()

            # Sync based on selected level
            if self.sync_level == 'full':
                if self.can_access_users:
                    self.sync_users()
                if self.can_access_projects:
                    self.sync_projects()
                self.sync_time_entries()
            elif self.sync_level == 'all_time':
                if self.can_access_projects:
                    self.sync_projects()
                self.sync_time_entries()
            else:  # my_time
                self.sync_my_time_entries()

            self.sudo().write({'last_sync': fields.Datetime.now()})
            self.env.cr.commit()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Harvest data synchronized successfully!'),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            self.env.cr.rollback()
            _logger.error(f'Sync failed: {str(e)}')
            raise UserError(_('Synchronization failed: %s') % str(e))

    def sync_users(self):
        try:
            response = requests.get(
                f'{self.api_url}users',
                headers=self._get_headers(),
                params={'is_active': 'true'},
                timeout=30
            )
            if response.status_code == 200:
                users_data = response.json()
                for user in users_data.get('users', []):
                    self._create_or_update_user(user)
            else:
                raise UserError(_('Failed to fetch users: %s') % response.text)
        except requests.RequestException as e:
            raise UserError(
                _('Network error while syncing users: %s') % str(e))
        except Exception as e:
            _logger.error(f'Failed to sync users: {str(e)}')
            raise

    def _create_or_update_user(self, harvest_user):
        HarvestUser = self.env['harvest.user']
        existing = HarvestUser.search(
            [('harvest_id', '=', str(harvest_user['id']))], limit=1)

        values = {
            'harvest_id': str(harvest_user['id']),
            'name': f"{harvest_user.get('first_name', '')} {harvest_user.get('last_name', '')}".strip(),
            'email': harvest_user.get('email'),
            'is_active': harvest_user.get('is_active', False),
            'config_id': self.id,
        }

        if existing:
            existing.write(values)
        else:
            employee = self.env['hr.employee'].search(
                [('work_email', '=', harvest_user.get('email'))], limit=1)
            if employee:
                values['employee_id'] = employee.id
            HarvestUser.create(values)

    def sync_projects(self):
        try:
            response = requests.get(
                f'{self.api_url}projects',
                headers=self._get_headers(),
                params={'is_active': 'true'},
                timeout=30
            )
            if response.status_code == 200:
                projects_data = response.json()
                for project in projects_data.get('projects', []):
                    self._create_or_update_project(project)
            else:
                raise UserError(_('Failed to fetch projects: %s') %
                                response.text)
        except requests.RequestException as e:
            raise UserError(
                _('Network error while syncing projects: %s') % str(e))
        except Exception as e:
            _logger.error(f'Failed to sync projects: {str(e)}')
            raise

    def _create_or_update_project(self, harvest_project):
        HarvestProject = self.env['harvest.project']
        existing = HarvestProject.search(
            [('harvest_id', '=', str(harvest_project['id']))], limit=1)

        values = {
            'harvest_id': str(harvest_project['id']),
            'name': harvest_project.get('name'),
            'code': harvest_project.get('code'),
            'is_active': harvest_project.get('is_active', False),
            'budget': harvest_project.get('budget'),
            'config_id': self.id,
        }

        if existing:
            existing.write(values)
        else:
            HarvestProject.create(values)

    def sync_time_entries(self):
        try:
            # Build params conditionally based on sync_all_dates setting
            params = {'page': 1, 'per_page': 100}

            if not self.sync_all_dates:
                date_from = fields.Date.today() - timedelta(days=self.sync_days_back)
                date_to = fields.Date.today()
                params.update({
                    'from': date_from.strftime('%Y-%m-%d'),
                    'to': date_to.strftime('%Y-%m-%d'),
                })

            page = 1
            while True:
                params['page'] = page
                response = requests.get(
                    f'{self.api_url}time_entries',
                    headers=self._get_headers(),
                    params=params,
                    timeout=30
                )

                if response.status_code == 200:
                    data = response.json()
                    for entry in data.get('time_entries', []):
                        self._create_or_update_time_entry(entry)

                    if page >= data.get('total_pages', 1):
                        break
                    page += 1
                else:
                    _logger.error(
                        f'Failed to sync time entries: {response.text}')
                    break

        except Exception as e:
            _logger.error(f'Failed to sync time entries: {str(e)}')

    def sync_my_time_entries(self):
        """Sync only the current user's time entries"""
        try:
            if not self.current_user_id:
                raise UserError(
                    _('Current user ID not found. Please check access levels first.'))

            # Build params conditionally
            params = {
                'user_id': self.current_user_id,
                'page': 1,
                'per_page': 100
            }

            if not self.sync_all_dates:
                date_from = fields.Date.today() - timedelta(days=self.sync_days_back)
                date_to = fields.Date.today()
                params.update({
                    'from': date_from.strftime('%Y-%m-%d'),
                    'to': date_to.strftime('%Y-%m-%d'),
                })

            # First, ensure we have the current user in our database
            self._ensure_current_user()

            # Sync projects that the user has time entries for
            self._sync_user_projects()

            page = 1
            while True:
                params['page'] = page
                response = requests.get(
                    f'{self.api_url}time_entries',
                    headers=self._get_headers(),
                    params=params,
                    timeout=30
                )

                if response.status_code == 200:
                    data = response.json()
                    for entry in data.get('time_entries', []):
                        self._create_or_update_time_entry(entry)

                    if page >= data.get('total_pages', 1):
                        break
                    page += 1
                else:
                    raise UserError(
                        _('Failed to fetch time entries: %s') % response.text)

        except requests.RequestException as e:
            raise UserError(
                _('Network error while syncing time entries: %s') % str(e))
        except Exception as e:
            _logger.error(f'Failed to sync my time entries: {str(e)}')
            raise

    def _ensure_current_user(self):
        """Ensure the current Harvest user exists in our database"""
        if not self.current_user_id:
            return

        try:
            response = requests.get(
                f'{self.api_url}users/me',
                headers=self._get_headers(),
                timeout=10
            )
            if response.status_code == 200:
                user_data = response.json()
                self._create_or_update_user(user_data)
        except:
            pass

    def _sync_user_projects(self):
        """Sync projects that the current user has time entries for"""
        try:
            # Get unique project info from user's time entries
            response = requests.get(
                f'{self.api_url}time_entries',
                headers=self._get_headers(),
                params={
                    'user_id': self.current_user_id,
                    'per_page': 100
                },
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                projects_info = {}

                # Collect project info from time entries
                for entry in data.get('time_entries', []):
                    if entry.get('project'):
                        project = entry['project']
                        project_id = str(project['id'])
                        if project_id not in projects_info:
                            projects_info[project_id] = project

                # Try to fetch full project details, fall back to proxy if needed
                for project_id, project_data in projects_info.items():
                    try:
                        # Try to get full project details
                        proj_response = requests.get(
                            f'{self.api_url}projects/{project_id}',
                            headers=self._get_headers(),
                            timeout=10
                        )
                        if proj_response.status_code == 200:
                            self._create_or_update_project(
                                proj_response.json())
                        else:
                            # Create proxy project with limited info
                            self._create_or_update_proxy_project(project_data)
                    except:
                        # Create proxy project with limited info
                        self._create_or_update_proxy_project(project_data)
        except:
            pass

    def _create_or_update_proxy_project(self, project_data):
        """Create or update a project with limited information from time entries"""
        HarvestProject = self.env['harvest.project']
        existing = HarvestProject.search(
            [('harvest_id', '=', str(project_data['id']))], limit=1)

        values = {
            'harvest_id': str(project_data['id']),
            'name': project_data.get('name', f"Project {project_data['id']}"),
            'code': project_data.get('code', ''),
            'is_active': True,
            'config_id': self.id,
        }

        if existing:
            # Only update if we have better info
            if project_data.get('name') and project_data['name'] != f"Project {project_data['id']}":
                existing.write({'name': project_data['name']})
            if project_data.get('code'):
                existing.write({'code': project_data['code']})
        else:
            HarvestProject.create(values)

    def _create_or_update_time_entry(self, harvest_entry):
        HarvestTimeEntry = self.env['harvest.time.entry']
        existing = HarvestTimeEntry.search(
            [('harvest_id', '=', str(harvest_entry['id']))], limit=1)

        # Handle user
        harvest_user = False
        if harvest_entry.get('user'):
            harvest_user = self.env['harvest.user'].search(
                [('harvest_id', '=', str(harvest_entry['user']['id']))], limit=1)
            if not harvest_user:
                # Create a proxy user if doesn't exist
                harvest_user = self.env['harvest.user'].create({
                    'harvest_id': str(harvest_entry['user']['id']),
                    'name': harvest_entry['user'].get('name', 'Unknown User'),
                    'config_id': self.id,
                })

        # Handle project - create proxy if doesn't exist
        harvest_project = False
        if harvest_entry.get('project'):
            project_data = harvest_entry['project']
            harvest_project = self.env['harvest.project'].search(
                [('harvest_id', '=', str(project_data['id']))], limit=1)

            if not harvest_project:
                # Create a proxy project with the limited info we have
                harvest_project = self.env['harvest.project'].create({
                    'harvest_id': str(project_data['id']),
                    'name': project_data.get('name', f"Project {project_data['id']}"),
                    'code': project_data.get('code', ''),
                    'is_active': True,
                    'config_id': self.id,
                })

        values = {
            'harvest_id': str(harvest_entry['id']),
            'spent_date': harvest_entry.get('spent_date'),
            'hours': harvest_entry.get('hours', 0.0),
            'notes': harvest_entry.get('notes'),
            'is_locked': harvest_entry.get('is_locked', False),
            'is_running': harvest_entry.get('is_running', False),
            'harvest_user_id': harvest_user.id if harvest_user else False,
            'harvest_project_id': harvest_project.id if harvest_project else False,
            'config_id': self.id,
        }

        if existing:
            existing.write(values)
        else:
            HarvestTimeEntry.create(values)

    @api.model
    def _cron_sync_harvest_data(self):
        """Cron job to automatically sync Harvest data for all active configurations"""
        active_configs = self.search([('active', '=', True)])

        for config in active_configs:
            try:
                _logger.info(
                    f'Starting scheduled Harvest sync for config: {config.account_id}')
                config.sync_harvest_data()
                _logger.info(
                    f'Completed scheduled Harvest sync for config: {config.account_id}')
            except Exception as e:
                _logger.error(
                    f'Failed scheduled Harvest sync for config {config.account_id}: {str(e)}')

        return True


class HarvestUser(models.Model):
    _name = 'harvest.user'
    _description = 'Harvest User'

    harvest_id = fields.Char('Harvest ID', required=True, index=True)
    name = fields.Char('Name', required=True)
    email = fields.Char('Email')
    employee_id = fields.Many2one('hr.employee', string='Employee')
    is_active = fields.Boolean('Active')
    config_id = fields.Many2one(
        'harvest.config', string='Configuration', required=True, ondelete='cascade')

    _sql_constraints = [
        ('harvest_id_uniq', 'unique(harvest_id, config_id)',
         'Harvest ID must be unique per configuration!')
    ]


class HarvestProject(models.Model):
    _name = 'harvest.project'
    _description = 'Harvest Project'

    harvest_id = fields.Char('Harvest ID', required=True, index=True)
    name = fields.Char('Name', required=True)
    code = fields.Char('Code')
    project_id = fields.Many2one('project.project', string='Odoo Project')
    is_active = fields.Boolean('Active')
    budget = fields.Float('Budget')
    config_id = fields.Many2one(
        'harvest.config', string='Configuration', required=True, ondelete='cascade')
    time_entry_ids = fields.One2many(
        'harvest.time.entry', 'harvest_project_id', string='Time Entries')

    _sql_constraints = [
        ('harvest_id_uniq', 'unique(harvest_id, config_id)',
         'Harvest ID must be unique per configuration!')
    ]

    def action_view_time_entries(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Time Entries'),
            'res_model': 'harvest.time.entry',
            'view_mode': 'tree,form',
            'domain': [('harvest_project_id', '=', self.id)],
            'context': {'default_harvest_project_id': self.id}
        }


class HarvestTimeEntry(models.Model):
    _name = 'harvest.time.entry'
    _description = 'Harvest Time Entry'
    _order = 'spent_date desc'

    harvest_id = fields.Char('Harvest ID', required=True, index=True)
    spent_date = fields.Date('Date', required=True)
    hours = fields.Float('Hours', required=True)
    notes = fields.Text('Notes')
    is_locked = fields.Boolean('Locked')
    is_running = fields.Boolean('Running')
    harvest_user_id = fields.Many2one(
        'harvest.user', string='Harvest User', required=True)
    harvest_project_id = fields.Many2one(
        'harvest.project', string='Harvest Project', required=False)
    timesheet_id = fields.Many2one(
        'account.analytic.line', string='Timesheet Entry')
    config_id = fields.Many2one(
        'harvest.config', string='Configuration', required=True, ondelete='cascade')

    _sql_constraints = [
        ('harvest_id_uniq', 'unique(harvest_id, config_id)',
         'Harvest ID must be unique per configuration!')
    ]

    def create_timesheet_entries(self):
        """Launch wizard for timesheet creation"""
        # Get entries that can create timesheets
        eligible_entries = self.filtered(
            lambda e: not e.timesheet_id
            and e.harvest_user_id.employee_id
            and e.harvest_project_id.project_id
        )

        if not eligible_entries:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Info'),
                    'message': _('No new timesheet entries to create.'),
                    'type': 'info',
                    'sticky': False,
                }
            }

        # Create wizard with selected entries
        wizard = self.env['harvest.timesheet.wizard'].create({
            'harvest_entry_ids': [(6, 0, eligible_entries.ids)]
        })

        return {
            'type': 'ir.actions.act_window',
            'name': _('Create Timesheets'),
            'res_model': 'harvest.timesheet.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }
