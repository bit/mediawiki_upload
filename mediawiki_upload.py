#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4
# GPL 3+ 2011
import cookielib
import itertools
import json
import mimetools
import mimetypes
import os
import re
import shutil
import tempfile
import urllib2
import webbrowser
from StringIO import StringIO


__version__ = 0.1

DEBUG=1
USER_AGENT='mediawiki_upload/%s (+http://www.mediawiki.org/wiki/User:BotInc/mediawiki_upload)' % __version__
DESCRIPTION = '''
== {{int:filedesc}} ==
{{Information
|Description={{%(description)s}}
|Source={{Own}}
|Author=%(author)s
|Date=%(date)s
|Permission=
|other_versions=
}}

<!--{{ImageUpload|full}}-->
== {{int:license}} ==
{{self|cc-by-sa-3.0,2.5,2.0,1.0}}
'''

class MultiPartForm(object):
    """Accumulate the data to be used when posting a form."""

    def __init__(self):
        self.form_fields = []
        self.files = []
        self.boundary = mimetools.choose_boundary()
        return
    
    def get_content_type(self):
        return 'multipart/form-data; boundary=%s' % self.boundary

    def add_field(self, name, value):
        """Add a simple field to the form data."""
        if isinstance(name, unicode):
            name = name.encode('utf-8')
        if isinstance(value, unicode):
            value = value.encode('utf-8')
        self.form_fields.append((name, value))
        return

    def add_file(self, fieldname, filename, fileHandle, mimetype=None):
        """Add a file to be uploaded."""
        if isinstance(fieldname, unicode):
            fieldname = fieldname.encode('utf-8')
        if isinstance(filename, unicode):
            filename = filename.encode('utf-8')
        if hasattr(fileHandle, 'read'):
            body = fileHandle.read()
        else:
            body = fileHandle
        if mimetype is None:
            mimetype = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        self.files.append((fieldname, filename, mimetype, body))
        return
    
    def __str__(self):
        """Return a string representing the form data, including attached files."""
        # Build a list of lists, each containing "lines" of the
        # request.  Each part is separated by a boundary string.
        # Once the list is built, return a string where each
        # line is separated by '\r\n'.  
        parts = []
        part_boundary = '--' + self.boundary
        
        # Add the form fields
        parts.extend(
            [ part_boundary,
              'Content-Disposition: form-data; name="%s"' % name,
              '',
              value,
            ]
            for name, value in self.form_fields
            )
        
        # Add the files to upload
        parts.extend(
            [ part_boundary,
              'Content-Disposition: file; name="%s"; filename="%s"' % \
                 (field_name, filename),
              'Content-Type: %s' % content_type,
              '',
              body,
            ]
            for field_name, filename, content_type, body in self.files
            )
        
        # Flatten the list and add closing boundary marker,
        # then return CR+LF separated data
        flattened = list(itertools.chain(*parts))
        flattened.append('--' + self.boundary + '--')
        flattened.append('')
        return '\r\n'.join(flattened)

class Mediawiki(object):
    def __init__(self, url, username, password):
        self.url = url
        self.username = username
        self.password = password

        self.cj = cookielib.CookieJar()
        self.opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(self.cj),
                                           urllib2.HTTPHandler(debuglevel=0))
        self.opener.addheaders = [
	        ('User-Agent', USER_AGENT)
        ]
        r = self.login()
        if not r['login']['result'] == 'Success':
            print r
            raise Exception('login failed')

    def post(self, form):
        try:
            request = urllib2.Request(self.url)
            body = str(form)
            request.add_header('Content-type', form.get_content_type())
            request.add_header('Content-length', len(body))
            request.add_data(body)
            result = self.opener.open(request).read().strip()
            return json.loads(result)
        except urllib2.HTTPError, e:
            if DEBUG:
                if e.code >= 500:
                    with open('/tmp/error.html', 'w') as f:
                        f.write(e.read())
                    #webbrowser.open_new_tab('/tmp/error.html')
            result = e.read()
            try:
                result = json.loads(result)
            except:
                result = {'status':{}}
            result['status']['code'] = e.code
            result['status']['text'] = str(e)
            return result

    def api(self, action, data={}, files={}):
        form = MultiPartForm()
        form.add_field('format', 'json')
        form.add_field('action', action)
        for key in data:
            form.add_field(key, data[key])
        for key in files:
            if isinstance(files[key], basestring):
                form.add_file(key, os.path.basename(files[key]), open(files[key]))
            else:
                form.add_file(key, 'data.bin', files[key])
        return self.post(form)

    def login(self):
        form = MultiPartForm()
        form.add_field('format', 'json')
        form.add_field('action','login')
        form.add_field('lgname', self.username)
        form.add_field('lgpassword', self.password)
        r = self.post(form)
        self.token = r['login']['token']
        self.sessionid = r['login']['sessionid']
        return self.api('login', {
            'lgname': self.username,
            'lgpassword': self.password,
            'lgtoken': self.token
        })

    def get_token(self, page, intoken='edit'):
        return str(self.api('query', {
            'prop': 'info',
            'titles': page,
            'intoken': intoken
        })['query']['pages']['-1']['edittoken'])

    def upload(self, filename, comment, text):
        CHUNKSIZE = 1024*1024 #1Mb
        offset = 0
        fn = os.path.basename(filename)
        pagename = 'File:' + fn.replace(' ', '_')
        token = self.get_token(pagename, 'edit')
        chunk = StringIO()
        filesize = os.stat(filename).st_size
        f = open(filename)
        f.seek(offset)
        chunk.write(f.read(CHUNKSIZE))
        f.close()
        chunk.seek(0)
        r = self.api('upload', {
            'comment': comment,
            'filename': fn,
            'filesize': str(filesize),
            'offset': str(offset),
            'token': token
        }, {'chunk': chunk})
        print r 
        offset += CHUNKSIZE
        filekey = r['upload']['filekey']
        while offset < filesize:
            chunk = StringIO()
            f = open(filename)
            f.seek(offset)
            chunk.write(f.read(CHUNKSIZE))
            f.close()
            chunk.seek(0)
            r = self.api('upload', {
                'filename': fn,
                'filesize': str(filesize),
                'offset': str(offset),
                'filekey': filekey,
                'token': token
            }, {'chunk': chunk})
            offset += CHUNKSIZE
            print r
            if 'error' in r or r.get('status', {}).get('code', 200) != 200 or \
                'error' in r.get('upload', {}):
                return r
        #Finalize upload and move out of stash
        r = self.api('upload', {
            'filename': fn,
            'filekey': filekey,
            'token': token,
            'text': text,
            'comment': comment
        })
        #print r
        return r

    def edit_page(self, pagename, text, comment=''):
        token = self.get_token(pagename, 'edit')
        return self.api('edit', {
            'comment': comment,
            'text': text,
            'title': pagename,
            'token': token
        })

def safe_name(s):
    s = s.strip()
    s = s.replace(' ', '_')
    s = re.sub(r'[:/\\]', '_', s)
    s = s.replace('__', '_').replace('__', '_')
    return s

def upload_file(filename, username, password, mediawiki_url, info={}):
    wiki = Mediawiki(mediawiki_url, username, password)
    #description = DESCRIPTION % info
    description = DESCRIPTION
    r = wiki.upload(filename, 'Initial Upload', description)
    print r
    if r['upload']['result'] == 'Success':
        print 'Uploaded to', r['upload']['imageinfo']['descriptionurl']
    else:
        print 'Upload failed.'

if __name__ == "__main__":
    from optparse import OptionParser
    import sys

    usage = "Usage: %prog [options] filename"
    parser = OptionParser(usage=usage)
    parser.add_option('-u', '--username', dest='username', help='wiki username', type='string')
    parser.add_option('-p', '--password', dest='password', help='wiki password', type='string')
    parser.add_option('-w', '--url', dest='url',
                     help='wiki api url [default:http://commons.wikimedia.org/w/api.php]',
                     default='http://commons.wikimedia.org/w/api.php', type='string')
    parser.add_option('-l', '--license', dest='license',
                     help='',
                     default='CC-BY-SA-3.0', type='string')

    (opts, args) = parser.parse_args()

    if None in (opts.username, opts.password) or not args:
        parser.print_help()
        sys.exit(-1)
    filename = args[0]
    upload_file(filename, opts.username, opts.password, opts.url, opts.license)
