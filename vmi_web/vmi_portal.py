# -*- encoding: utf-8 -*-

import glob
import itertools
import json
import operator
import os
import cStringIO
import urllib
import urllib2
import xmlrpclib
import zlib
import simplejson
import base64
import logging
from xml.etree import ElementTree
from simpletal import simpleTAL, simpleTALES
import werkzeug.utils
import werkzeug.wrappers
import openerp
from openerp.tools.translate import _
import openerp.addons.web.http as vmiweb

_logger = logging.getLogger(__name__)

# -----------------------------------------------| VMI Global Methods.


def fields_get(req, model):
    Model = req.session.model(model)
    fields = Model.fields_get(False, req.context)
    #_logger.debug('fields: %s', fields)
    return fields

#				 (req, 'dbe.vendor', ['id', 'company'], 0, False, [('vuid', '=', uid)], None)
def do_search_read(req, model, fields=False, offset=0, limit=False, domain=None, sort=None):
    """ Performs a search() followed by a read() (if needed) using the
provided search criteria

:param req: a JSON-RPC request object
:type req: vmiweb.JsonRequest
:param str model: the name of the model to search on
:param fields: a list of the fields to return in the result records
:type fields: [str]
:param int offset: from which index should the results start being returned
:param int limit: the maximum number of records to return
:param list domain: the search domain for the query
:param list sort: sorting directives
:returns: A structure (dict) with two keys: ids (all the ids matching
                    the (domain, context) pair) and records (paginated records
                    matching fields selection set)
:rtype: list
"""
    Model = req.session.model(model)

    ids = Model.search(domain, offset or 0, limit or False, sort or False,
                       req.context)
    if limit and len(ids) == limit:
        length = Model.search_count(domain, req.context)
    else:
        length = len(ids) + (offset or 0)
    if fields and fields == ['id']:
        # shortcut read if we only want the ids
        return {
        'length': length,
        'records': [{'id': id} for id in ids]
        }

    records = Model.read(ids, fields or False, req.context)
    records.sort(key=lambda obj: ids.index(obj['id']))
    return {
    'length': length,
    'records': records
    }


def newSession(req):
    """ create admin session for testing purposes only """
    db = 'dev_main'
    login = 'admin'
    password = 'openerp'
    uid = req.session.authenticate(db, login, password)
    return uid


def check_partner_parent(pid):
    res = None
    parent_id = None
    try:
        res = do_search_read(req, 'res.partner', ['active', 'parent_id'], 0, False, [('id', '=', pid)], None)
    except Exception:
        _logger.debug('Session expired or Partner not found for partner ID: %s', pid)

    if res:
        record = res['records'][0]
        if record['parent_id'] and record['active']:
            parent_id = record['parent_id']
        else:
            raise Exception("AccessDenied")
    else:
        return False

    return parent_id


def get_partner(req, pid):
    partner = None
    fields = fields_get(req, 'res.partner')
    try:
        partner = do_search_read(req, 'res.partner', fields, 0, False, [('id', '=', pid)], None)
    except Exception:
        _logger.debug('Partner not found for ID: %s', pid)

    if not partner:
        raise Exception("AccessDenied")

    return partner


def get_vendor_id(req, uid=None, **kwargs):
    """ Find the vendor associated to the current logged-in user """
    vendor_ids = None
    try:
        vendor_ids = do_search_read(req, 'dbe.vendor', ['id', 'company'], 0, False, [('vuid.id', '=', uid)], None)
    except Exception:
        _logger.debug('Session expired or Vendor not found for user ID: %s', uid)

    if not vendor_ids:
        raise Exception("AccessDenied")

    _logger.debug('Vendor ID: %s',
                  vendor_ids) #{'records': [{'company': u'Gomez Electrical Supply', 'id': 3}], 'length': 1}
    return vendor_ids

def get_partner_id(req, uid=None, **kwargs):
    """ Find the partner associated to the current logged-in user """
    partner_ids = None
    try:
        partner_ids = do_search_read(req, 'res.users', ['partner_id'], 0, False, [('id', '=', uid)], None)
    except Exception:
        _logger.debug('Session expired or Partner not found for user ID: %s', uid)

    if not partner_ids:
        raise Exception("AccessDenied")

    record = partner_ids['records'][0]
    pid = record['partner_id'][0]
    parent_id = check_partner_parent(pid)
    if parent_id:
        p = get_partner(parent_id)
        parent = p['records'][0]
        record['company'] = parent['name']
        record['company_id'] = parent['id']
        partner_ids['records'].append(record)
        partner_ids.pop(0)

    _logger.debug('Partner ID: %s',
                  partner_ids) #{'records': [{'groups_id': [3, 9, 19, 20, 24, 27], 'partner_id': (20, u'Partner'), 'id': 13, 'name': u'Partner'}], 'length': 1}
    return partner_ids

# -----------------------------------------------| VMI Session Object.
class Session(vmiweb.Controller):
    _cp_path = "/vmi/session"

    def session_info(self, req):
        req.session.ensure_valid()
        uid = req.session._uid
        args = req.httprequest.args
        request_id = str(req.jsonrequest['id'])
        _logger.debug('JSON Request ID: %s', request_id)
        res = {}
        if request_id == 'DBE': # Check to see if user is a DBE vendor
            try:                                                                # Get vendor ID for session
                vendor = get_vendor_id(req, uid)['records'][0]
            except IndexError:
                _logger.debug('Vendor not found for user ID: %s', uid)
                return {'error': _('No Vendor found for this User ID!'), 'title': _('Vendor Not Found')}
            res = {
            "session_id": req.session_id,
            "uid": req.session._uid,
            "user_context": req.session.get_context() if req.session._uid else {},
            "db": req.session._db,
            "username": req.session._login,
            "vendor_id": vendor['id'],
            "company": vendor['company'],
            }
        elif request_id == 'VMI': # Check to see if user is a VMI vendor
            try:                                                                                # Get Partner ID for session
                vendor = get_partner_id(req, uid)['records'][0]
            except IndexError:
                _logger.debug('Partner not found for user ID: %s', uid)
                return {'error': _('No Partner found for this User ID!'), 'title': _('Partner Not Found')}
            company = ""
            if vendor.has_key('company'):
                company = vendor['company']
            res = {
            "session_id": req.session_id,
            "uid": req.session._uid,
            "user_context": req.session.get_context() if req.session._uid else {},
            "db": req.session._db,
            "username": req.session._login,
            "partner_id": vendor['partner_id'][0],
            "company": vendor['partner_id'][1],
            }
        else: # Allow login for valid user without Vendor or Partner such as Admin or Manager
            res = {
            "session_id": req.session_id,
            "uid": req.session._uid,
            "user_context": req.session.get_context() if req.session._uid else {},
            "db": req.session._db,
            "username": req.session._login,
            }
        return res

    @vmiweb.jsonrequest
    def get_session_info(self, req):
        return self.session_info(req)

    @vmiweb.jsonrequest
    def authenticate(self, req, db, login, password, base_location=None):
        wsgienv = req.httprequest.environ
        env = dict(
            base_location=base_location,
            HTTP_HOST=wsgienv['HTTP_HOST'],
            REMOTE_ADDR=wsgienv['REMOTE_ADDR'],
        )
        req.session.authenticate(db, login, password, env)

        return self.session_info(req)

    @vmiweb.jsonrequest
    def change_password(self, req, fields):
        old_password, new_password, confirm_password = operator.itemgetter('old_pwd', 'new_password', 'confirm_pwd')(
            dict(map(operator.itemgetter('name', 'value'), fields)))
        if not (old_password.strip() and new_password.strip() and confirm_password.strip()):
            return {'error': _('You cannot leave any password empty.'), 'title': _('Change Password')}
        if new_password != confirm_password:
            return {'error': _('The new password and its confirmation must be identical.'),
                    'title': _('Change Password')}
        try:
            if req.session.model('res.users').change_password(
                    old_password, new_password):
                return {'new_password': new_password}
        except Exception:
            return {'error': _('The old password you provided is incorrect, your password was not changed.'),
                    'title': _('Change Password')}
        return {'error': _('Error, password not changed !'), 'title': _('Change Password')}


    @vmiweb.jsonrequest
    def check(self, req):
        req.session.assert_valid()
        return None

    @vmiweb.jsonrequest
    def destroy(self, req):
        req.session._suicide = True

# -----------------------------------------------| VMI Controller Methods.
class VmiController(vmiweb.Controller):
    _cp_path = '/vmi'

    @vmiweb.httprequest
    def index(self, req, mod=None, **kwargs):
        js = """

$(document).ready(function(){
	$("form#loginForm").submit(function() { // loginForm is submitted
	var username = $('#username').attr('value'); // get username
	var password = $('#password').attr('value'); // get password


	if (username && password) { // values are not empty
		$.ajax({
		type: "POST",
		url: "/vmi/session/authenticate", // URL of OpenERP Authentication Handler
		contentType: "application/json; charset=utf-8",
		dataType: "json",
		// send username and password as parameters to OpenERP
		data:	 '{"jsonrpc": "2.0", "method": "call", "params": {"session_id": null, "context": {}, "login": "' + username + '", "password": "' + password + '", "db": "dev_main"}, "id": "VMI"}',
		// script call was *not* successful
		error: function(XMLHttpRequest, textStatus, errorThrown) {
			$('div#loginResult').text("responseText: " + XMLHttpRequest.responseText
			+ ", textStatus: " + textStatus
			+ ", errorThrown: " + errorThrown);
			$('div#loginResult').addClass("error");
		}, // error
		// script call was successful
		// data contains the JSON values returned by OpenERP
		success: function(data){
			if (data.result.error) { // script returned error
			$('div#loginResult').text("data.result.title: " + data.result.error);
			$('div#loginResult').addClass("error");
			} // if
			else { // login was successful
			$('form#loginForm').hide();
			$('div#loginResult').html("<h2>Success!</h2> "
				+ " Welcome <b>" + data.result.company + "</b>");
			$('div#loginResult').addClass("success");
			responseData = data.result;
			sessionid = data.result.session_id;
			$('div#vmi_menu').fadeIn();
			} //else
		} // success
		}); // ajax
	} // if
	else {
		$('div#loginResult').text("enter username and password");
		$('div#loginResult').addClass("error");
	} // else
	$('div#loginResult').fadeIn();
	return false;
	});
});

		"""
        input = open(
            '/home/amir/dev/parts/openerp-7.0-20131118-002448/openerp/addons/vmi/vmi_web/template/index.html', 'r')
        template = simpleTAL.compileHTMLTemplate(input)
        input.close()

        context = simpleTALES.Context()
        # Add a string to the context under the variable title
        context.addGlobal("title", "SEPTA VMI Client")
        context.addGlobal("script", js)

        output = cStringIO.StringIO()
        template.expand(context, output)
        return output.getvalue()
