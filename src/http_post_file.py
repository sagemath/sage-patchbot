# http://code.activestate.com/recipes/146306-http-client-to-post-using-multipartform-data/

try:
    from urllib2 import urlopen, Request  # python2
except ImportError:
    from urllib.request import urlopen, Request  # python3

import mimetypes
# import mimetools  # python 2 only
import string
import random


def id_generator(size=26, chars=string.ascii_uppercase + string.digits):
    """
    substitute for mimetools.choose_boundary()
    """
    return ''.join(random.choice(chars) for _ in range(size))


def post_multipart(url, fields, files):
    """
    Post fields and files to an http host as multipart/form-data.
    fields is a sequence of (name, value) elements for regular form
    fields.  files is a sequence of (name, filename, value) elements
    for data to be uploaded as files

    Return the server's response page.
    """
    content_type, body = encode_multipart_formdata(fields, files)
    headers = {'Content-Type': content_type,
               'Content-Length': str(len(body))}
    r = Request(url, body, headers)
    return urlopen(r).read()


def encode_multipart_formdata(fields, files):
    """
    fields is a sequence of (name, value) elements for regular form
    fields.  files is a sequence of (name, filename, value) elements
    for data to be uploaded as files

    Return (content_type, body) ready for httplib.HTTP instance
    """
    # BOUNDARY = mimetools.choose_boundary()
    BOUNDARY = id_generator()
    CRLF = '\r\n'
    L = []
    if isinstance(fields, dict):
        fields = fields.items()
    for (key, value) in fields:
        L.append('--' + BOUNDARY)
        L.append('Content-Disposition: form-data; name="{}"'.format(key))
        L.append('')
        L.append(value)
    for (key, filename, value) in files:
        L.append('--' + BOUNDARY)
        cont = 'Content-Disposition: form-data; name="{}"; filename="{}"'
        L.append(cont.format(key, filename))
        L.append('Content-Type: {}'.format(get_content_type(filename)))
        L.append('')
        L.append(value)
    L.append('--' + BOUNDARY + '--')
    L.append('')
    body = CRLF.join(L)
    content_type = 'multipart/form-data; boundary={}'.format(BOUNDARY)
    return content_type, body


def get_content_type(filename):
    return mimetypes.guess_type(filename)[0] or 'application/octet-stream'
