import pika
from pika.exceptions import *
import logging
import yaml
import sys
import toolsmod
from toolsmod import L1Exception
from toolsmod import L1MessageError
from XMLHandler import * 
from YamlHandler import * 

LOG_FORMAT = ('%(levelname) -10s %(asctime)s %(name) -30s %(funcName) '
              '-35s %(lineno) -5d: %(message)s')
LOGGER = logging.getLogger(__name__)


class SimplePublisher:

  EXCHANGE = 'message'

  def __init__(self, amqp_url, formatOptions=None):

    self._connection = None
    self._channel = None
    self._message_number = 0
    self._stopping = False
    self._url = amqp_url
    self._closing = False
    self._xml_handler = None
    self._format_options = formatOptions

    if formatOptions == "XML":
        self._message_handler = XMLHandler()
    else:
        self._message_handler = YamlHandler()

    try:
       self.connect()
    except:
       LOGGER.error('No channel - connection channel is None')
       

  def connect(self):
    self._connection = pika.BlockingConnection(pika.URLParameters(self._url))
    self._channel = self._connection.channel()
    if self._channel == None:
      LOGGER.error('No channel - connection channel is None')

  def publish(self, route_key, msg): 
      try: 
          self._channel.basic_publish(exchange=self.EXCHANGE, routing_key=route_key, body=msg)
      except pika.exceptions.ConnectionClosed: 
          LOGGER.critical("Connection timed out. Reconnected and republish message")
          self.connect()
          self.publish(route_key, msg)

  def publish_message(self, route_key, msg):
    if self._channel == None or self._channel.is_closed == True:
       try:
         self.connect()
       except AMQPError as e:
         LOGGER.critical('Unable to create connection to rabbit server. Heading for exit...')
         sys.exit(105)

    LOGGER.debug ("Sending msg to %s", route_key)

    if self._format_options == "XML":
        try:
            xmlRoot = self._xml_handler.encodeXML(msg)
            valid = self._xml_handler.validate(xmlRoot)
            if valid: 
                xmlMsg = self._xml_handler.tostring(xmlRoot)
                self.publish(route_key, xmlMsg)
            else: 
                raise L1MessageError("Message is invalid XML.")
        except L1MessageError as e:
            raise L1MessageError("Message is invalid XML.")
    else: 
        #print "In Simple Publisher, route_key is %s" % str(route_key)
        #print "In Simple Publisher, msg is %s" % str(msg)
        yamldict = self._message_handler.encode_message(msg)
        #print "In Simple Publisher,  fter encoding message, yamldict is %s" % str(yamldict)
        self.publish(route_key, yamldict)
