import sys
import json
import logging.config
import pycurl

from urllib import urlencode
from cStringIO import StringIO
from pprint import pprint as pp
from flask import Flask, request, abort


APP_CONFIG = 'bot.cfg'
LOGGING_CONFIG = 'bot_logging.cfg'
BASE_URL = 'https://api.telegram.org/bot'
GOOGLE_URL = 'http://finance.google.com/finance/info?client=ig&q='


### logging

logging.config.fileConfig(LOGGING_CONFIG, disable_existing_loggers=False)

### flask app init

app = Flask(__name__)

try:
    app.config.from_pyfile(APP_CONFIG)
except IOError, e:
    app.logger.critical("No configuration file found: %s" % e)
    raise SystemExit(1)


if not app.config.get('TOKEN'):
    app.logger.critical("No 'TOKEN' key found in app.config")
    raise SystemExit(1)


### commands registry

class BotCommands(object):
    
    def __init__(self):
        self._funcs = {}
        self._docs = {}
        self.min_length = len('start')
        
    def get_command(self, name):
        return self._funcs.get(name)
        
    def get_docs(self):
        return sorted(self._docs.items())
        
    def _wrapper(self, f, doc=None, *args):
        name = f.func_name
        if not name.startswith('_do_'):
            raise SystemExit("The bot_command decorator expects a function name starting with _do_")
        name = name[4:]
        self._funcs[name] = f
        if doc is not None:
            self._docs[name] = (doc, args)
        self.min_length = min((self.min_length, len(name)))
        return f
        
    def register(self, f, *args):
        
        if callable(f):
            return self._wrapper(f)
            
        def _wrapper(func):
            return self._wrapper(func, f, *args)
            
        return _wrapper


commands = app.config.bot_commands = BotCommands()


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

    app.logger.debug(url)

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
        result = json.loads(result, encoding='iso-8859-15')
    except ValueError, e:
        logger.debug(type(result))
        logger.debug(result)
        raise ValueError('error decoding result: %s' % e)
    
    if len(result) == 0:
        raise ValueError('no results')
        
    return result


@commands.register
def _do_start(*args):
    
    return 'Welcome!\nThis bot supports the following commands:\n' + _do_help()


@commands.register
def _do_help(*args):
    
    buffer = []

    for name, docdata in app.config.bot_commands.get_docs():
        
        doc, docargs = docdata
        
        buffer.append('/%s%s - %s' % (
            name,
            '' if not docargs else (' ' + ', '.join(docargs)),
            doc
        ))
    
    buffer.append('/help - this command')
    
    return '\n'.join(buffer)


@commands.register('3-day chart for STOCK', 'STOCK')
def _do_chart(*args):

    if len(args) == 0:
        return 'missing stock symbol'
    
    return 'http://chart.finance.yahoo.com/z?s=%s&t=3d&q=c&l=on&z=l' % args[0]


@commands.register('main currencies')
def _do_currencies(*args):
    
    return _do_quote('CURRENCY:EURUSD', 'CURRENCY:EURGBP', 'CURRENCY:USDGBP').replace('CURRENCY:', '')


@commands.register('main indexes')
def _do_indexes(*args):
    
    mapping = {
        '.INX': 'S&P 500',
        'NDX': 'Nasdaq 100',
        '.IXIC': 'Nasdaq Composite',
        'FTSEMIB': 'FTSE MIB',
        'INDEXFTSE:UKX': 'FTSE 100',
        'INDEXSTOXX:SX5E': 'ESTX 50 PR.EUR',
        'INDEXDB:DAX': 'DAX',
        'EURUSD': 'Euro/USD',
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


@commands.register('quotes for STOCK [STOCK...]', 'STOCK [STOCK...]')
def _do_quote(*args):
    
    # http://www.quora.com/What-do-the-following-attributes-returned-in-a-JSON-array-from-the-Google-Finance-API-mean
    
    try:
        result = _google_finance_request(*args)
    except ValueError, e:
        return e.args[0]
        
    buffer = []
    
    for r in result:
        app.logger.debug(', '.join(r.keys()))
        buffer.append(
            '%(e)s:%(t)s %(l)s %(cp)s%% %(lt)s' % r # \next hours %(el)s %(ecp)s%% %(elt)s
        )
    
    return '\n'.join(buffer)
    

@app.route("/ludo/", methods=['POST'])
def ludobot():
    
    # TODO: keep utf-8 encoded text instead of decoding then re-encoding
    try:
        data = json.loads(request.data)
    except ValueError, e:
        app.logger.critical("error decoding webhook json data: %s" % e)
        app.logger.debug(request.data)
        abort(400)

    if app.debug:
        print >>sys.stderr, '\n\n --- webhook received --- \n'
        pp(data, stream=sys.stderr)
    
    text = data['message'].get('text')
    username = data['message'].get('from', {}).get('username')
    chat = data['message'].get('chat', {}).get('title', '[direct message]')
    
    if text is None:
        return ''
    
    if isinstance(text, unicode):
        text = text.encode('utf-8')
    if isinstance(username, unicode):
        username = username.encode('utf-8')
    if isinstance(chat, unicode):
        chat = chat.encode('utf-8')
    
    app.logger.info("user %s in chat %s %s" % (username, chat, text))

    if len(text) < app.config.bot_commands.min_length:
        return ''

    if text.startswith('/'):
        
        tokens = text[1:].split()
        command_name = tokens[0]
        if '@' in command_name:
           command_name, sep, bot_name = command_name.partition('@') 

        app.logger.debug(commands._funcs.keys())
        if tokens:
            func = app.config.bot_commands.get_command(command_name)
            if func:
                output = func(*tokens[1:])
            else:
                app.logger.debug("Unrecognized command '%s'" % ' '.join(tokens))
                return ''
        else:
            app.logger.debug("Empty command")
            return ''

    else:
        output = 'What are you trying to say?'
        
    app.logger.debug("sending response '%s'" % output)

    try:
        response = get(_telegram_api_url('sendMessage'), params={
            'chat_id':data['message']['chat']['id'],
            'text': output
        })
    except ValueError, e:
        app.logger.critical("Error sending response: %s" % e.args[0])
    else:
        if app.debug:
            print >>sys.stderr, "\n\n --- client received -- \n\n"
            pp(response, stream=sys.stderr)
        try:
            data = json.loads(response)
        except ValueError, e:
            app.logger.warning("Error parsing json command response: %s" % e)
        else:
            if not data.get('ok'):
                app.logger.warning("Error in json response: %s" % data)
    
    
    return  ''


if __name__ == "__main__":
    app.run(port=9002, debug=True)
