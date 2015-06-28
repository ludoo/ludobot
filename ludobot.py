import sys
import logging
import json
import pycurl

from urllib import urlencode
from cStringIO import StringIO
from pprint import pprint as pp
from flask import Flask, request, abort


APP_CONFIG = 'bot.cfg'
BASE_URL = 'https://api.telegram.org/bot'
GOOGLE_URL = 'http://finance.google.com/finance/info?client=ig&q='
DEBUG = False


### logging

logging.basicConfig(
    format="%(asctime)-15s %(levelname)s %(message)s",
    level=logging.INFO if not DEBUG else logging.DEBUG,
    stream=sys.stdout
)
logger = logging.getLogger()


### flask app init

app = Flask(__name__)
try:
    app.config.from_pyfile(APP_CONFIG)
except IOError, e:
    logger.critical("No configuration file found: %s" % e)
    raise SystemExit(1)


if not app.config.get('TOKEN'):
    logger.critical("No 'TOKEN' key found in app.config")
    raise SystemExit(1)


### HTTP get via pycurl (requests complains about ssl context, and returns errors)

def get(url, params=None, no_body=False):

    c = pycurl.Curl()
    b = StringIO()

    if no_body:
        c.setopt(pycurl.NOBODY, True)
    else:
        c.setopt(pycurl.WRITEFUNCTION, b.write)
    
    if isinstance(url, unicode):
        url = url.encode('utf-8')
    
    if params:
        quoted_params = urlencode(params)
        if '?' not in url:
            url += '?'
        elif not url.endswith('&') or not url.endswith('?'):
            url += '&'
        url += quoted_params

    logger.debug(url)

    c.setopt(pycurl.URL, url)
    c.setopt(pycurl.CONNECTTIMEOUT, 5)
    c.setopt(pycurl.TIMEOUT, 15)
    c.setopt(pycurl.FOLLOWLOCATION, 1)
    c.setopt(pycurl.MAXREDIRS, 2)
    c.setopt(pycurl.NOSIGNAL, 1)
    c.setopt(pycurl.ENCODING, 'utf-8')
    c.setopt(pycurl.SSL_VERIFYPEER, False)
    #c.setopt(pycurl.FAILONERROR, True)
    
    # if we cared about headers we would use pycurl.HEADERFUNCTION
    # and if we cared about proxies we would use pycurl.PROXY and pycurl.PROXYTYPE
    
    try:
        c.perform()
    except pycurl.error, e:
        #errno, errstr = e
        raise ValueError('Curl error: %s' % e)

    code = c.getinfo(pycurl.HTTP_CODE)
    
    if code == 200:
        if no_body:
            return ''

        b.seek(0)
        return b.read()

    raise ValueError('Response error: code %s' % code)


### internal functions

def _telegram_api_url(method_name):
    return ''.join((
        BASE_URL, app.config['TOKEN'], '/', method_name, '?'
    ))


def _google_finance_request(*args):
    
    if len(args) == 0:
        raise ValueError('missing stock symbol')
    
    response = get(GOOGLE_URL + ','.join(args))

    # fix the invalid response body
    result = response.replace('\n', '')
    if result.startswith('// '):
        result = result[3:]
    
    try:
        result = json.loads(result)
    except ValueError, e:
        raise ValueError('error decoding result: %s' % e)
    
    if len(result) == 0:
        raise ValueError('no results')
        
    return result


def bot_command(f, *args):
    
    if callable(f):
        f._command_description = None
        f._command_args = []
        return f
    
    def wrapper(func):
        func._command_description = f
        func._command_args = args
        return func
    
    return wrapper


def get_commands():
    mod = sys.modules[__name__]
    for name in dir(mod):
        if not name.startswith('_do'):
            continue
        func = getattr(mod, name)
        if not callable(func) or not hasattr(func, '_command_description'):
            continue
        yield name, func


@bot_command
def _do_start(*args):
    
    return 'Welcome!\nThis bot supports the following commands:\n' + _do_help()


@bot_command
def _do_help(*args):
    
    buffer = []

    for name, func in sorted(get_commands()):
        
        if not func._command_description:
            continue
        
        buffer.append('/%s%s - %s' % (
            func.func_name[4:],
            '' if not func._command_args else (' ' + ', '.join(func._command_args)),
            func._command_description
        ))
    
    buffer.append('/help - this command')
    
    return '\n'.join(buffer)


@bot_command('3-day chart for STOCK', 'STOCK')
def _do_chart(*args):

    if len(args) == 0:
        return 'missing stock symbol'
    
    return 'http://chart.finance.yahoo.com/z?s=%s&t=3d&q=c&l=on&z=l' % args[0]


@bot_command('main indexes')
def _do_indexes(*args):
    
    mapping = {
        '.INX': 'S&P 500',
        'NDX': 'Nasdaq 100',
        '.IXIC': 'Nasdaq Composite',
        'FTSEMIB': 'FTSE MIB',
    }
    
    try:
        result = _google_finance_request(*mapping.keys())
    except ValueError, e:
        return e.args[0]
    
    buffer = []
    
    for r in result:
        r['verbose_name'] = mapping.get(r['t'], r['t'])
        buffer.append(
            '%(verbose_name)s %(l)s %(cp)s%% %(lt)s' % r # \next hours %(el)s %(ecp)s%% %(elt)s
        )
    
    return '\n'.join(buffer)


@bot_command('quotes for STOCK [STOCK...]', 'STOCK [STOCK...]')
def _do_quote(*args):
    
    # http://www.quora.com/What-do-the-following-attributes-returned-in-a-JSON-array-from-the-Google-Finance-API-mean
    
    try:
        result = _google_finance_request(*args)
    except ValueError, e:
        return e.args[0]
        
    buffer = []
    
    for r in result:
        logger.debug(', '.join(r.keys()))
        buffer.append(
            '%(e)s:%(t)s %(l)s %(cp)s%% %(lt)s' % r # \next hours %(el)s %(ecp)s%% %(elt)s
        )
    
    return '\n'.join(buffer)
    

@app.route("/ludo/", methods=['POST'])
def ludobot():
    
    try:
        data = json.loads(request.data)
    except ValueError, e:
        logger.critical("error decoding webhook json data: %s" % e)
        logger.debug(request.data)
        abort(400)

    if DEBUG:
        print >>sys.stderr, '\n\n --- webhook received --- \n'
        pp(data, stream=sys.stderr)
    
    text = data['message'].get('text')
    
    if text is None:
        return ''
    
    if isinstance(text, unicode):
        text = text.encode('utf-8')
    
    logger.info("new text %s" % text)

    if text.startswith('/'):
        
        tokens = text[1:].split()
        
        func = globals().get('_do_%s' % tokens[0])
        if func and callable(func) and hasattr(func, '_command_description'):
            output = func(*tokens[1:])
        else:
            output = 'Unsupported command'
            
    else:

        output = 'What are you trying to say?'
            
    
    try:
        response = get(_telegram_api_url('sendMessage'), params={
            'chat_id':data['message']['chat']['id'],
            'text': output
        })
    except ValueError, e:
        logger.critical("Error sending response: %s" % e.args[0])
    else:
        if DEBUG:
            print >>sys.stderr, "\n\n --- client received -- \n\n"
            pp(response, stream=sys.stderr)
        try:
            data = json.loads(response)
        except ValueError, e:
            logger.warning("Error parsing json command response: %s" % e)
        else:
            if not data.get('ok'):
                logger.warning("Error in json response: %s" % data)
    
    
    return ""


if __name__ == "__main__":
    app.run(port=9002, debug=True)
