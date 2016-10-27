import toolsmod
from toolsmod import get_timestamp
import logging
import pika
import redis
import yaml
import sys
import os
import time
from time import sleep
import thread
from const import *
from Scoreboard import Scoreboard
from JobScoreboard import JobScoreboard
from AckScoreboard import AckScoreboard
from Consumer import Consumer
from SimplePublisher import SimplePublisher

LOG_FORMAT = ('%(levelname) -10s %(asctime)s %(name) -30s %(funcName) '
              '-35s %(lineno) -5d: %(message)s')
LOGGER = logging.getLogger(__name__)


class DMCS:
    JOB_SCBD = None
    ACK_SCBD = None
    CMD_ST_SCBD = None
    OCS_BDG_PUBLISH = "ocs_bdg_publish"  #Messages from OCS Bridge
    OCS_BDG_CONSUME = "ocs_bdg_consume"  #Messages to OCS Bridge
    DMCS_PUBLISH = "dmcs_publish" #Used for Foreman comm
    DMCS_CONSUME = "dmcs_consume" #Used for Foreman comm
    ACK_PUBLISH = "ack_publish"
    EXCHANGE = 'message'
    EXCHANGE_TYPE = 'direct'


    def __init__(self, filename=None):
        toolsmod.singleton(self)

        self.purge_broker()
        self._default_cfg_file = 'Dmcs_Cfg.yaml'
        if filename == None:
            filename = self._default_cfg_file

        cdm = self.intake_yaml_file(filename)

        try:
            self._base_name = cdm[ROOT][BASE_BROKER_NAME]      # Message broker user & passwd
            self._base_passwd = cdm[ROOT][BASE_BROKER_PASSWD]   
            self._base_broker_addr = cdm[ROOT][BASE_BROKER_ADDR]
            self._session_id_file = cdm[ROOT][SESSION_ID_FILE]
        except KeyError as e:
            print "Dictionary error"
            print "Bailing out..."
            sys.exit(99)

        self._base_broker_url = 'amqp_url'
        self._next_timed_ack_id = 0


        # Create Redis Forwarder table with Forwarder info

        self.JOB_SCBD = JobScoreboard()
        self.TO_DO_SCBD = ToDoScoreboard()
        self.ACK_SCBD = AckScoreboard()
        self.CMD_ST_SCBD = CommandStateScoreboard()
        # Messages from both Base Foreman AND OCS Bridge
        self._msg_actions = { 'COMMAND': self.process_command,
                              'EVENT': self.process_event,
                              'TELEMETRY': self.process_telemetry,
                              'FOREMAN_HEALTH_ACK': self.process_ack,
                              'FOREMAN_NEXT_VISIT_ACK': self.process_ack,
                              'FOREMAN_START_INTEGRATION_ACK': self.process_ack,
                              'FOREMAN_READOUT_ACK': self.process_ack,
                              'NEW_JOB_ACK': self.process_ack }


        self._base_broker_url = "amqp://" + self._base_name + ":" + self._base_passwd + "@" + str(self._base_broker_addr)
        LOGGER.info('Building _base_broker_url. Result is %s', self._base_broker_url)

        self._session_id = self.get_session_id(self._session_id_file)
        self.setup_publishers()
        self.setup_consumers()



    def get_session_id(self, filename):
        last_session = self.intake_yaml(filename)
        current_session = int(last_session[SESSION_ID]) + 1
        session_dict = {}
        session_dict[SESSION_ID] = current_session
        session_dict[SESSION_START_TIMESTAMP] = get_timestamp()
        self.export_yaml(filename, session_dict)
        return current_session

        

    def setup_consumers(self):
        LOGGER.info('Setting up consumers on %s', self._base_broker_url)
        LOGGER.info('Running start_new_thread on all consumer methods')

        self._ocs_bdg_consumer = Consumer(self._base_broker_url, self.OCS_BDG_PUBLISH, XML)
        try:
            thread.start_new_thread( self.run_dmcs_consumer, ("thread-dmcs-consumer", 2,) )
        except:
            LOGGER.critical('Cannot start DMCS consumer thread, exiting...')
            sys.exit(99)

        self._forwarder_consumer = Consumer(self._base_broker_url, self.FORWARDER_PUBLISH, XML)
        try:
            thread.start_new_thread( self.run_forwarder_consumer, ("thread-forwarder-consumer", 2,) )
        except:
            LOGGER.critical('Cannot start FORWARDERS consumer thread, exiting...')
            sys.exit(100)

        self._ncsa_consumer = Consumer(self._base_broker_url, self.NCSA_PUBLISH, XML)
        try:
            thread.start_new_thread( self.run_ncsa_consumer, ("thread-ncsa-consumer", 2,) )
        except:
            LOGGER.critical('Cannot start NCSA consumer thread, exiting...')
            sys.exit(101)

        self._ack_consumer = Consumer(self._base_broker_url, self.ACK_PUBLISH, XML)
        try:
            thread.start_new_thread( self.run_ack_consumer, ("thread-ack-consumer", 2,) )
        except:
            LOGGER.critical('Cannot start ACK consumer thread, exiting...')
            sys.exit(102)

        LOGGER.info('Finished starting all three consumer threads')


    def run_dmcs_consumer(self, threadname, delay):
        self._foreman_consumer.run(self.on_dmcs_message)


    def run_ocs_consumer(self, threadname, delay):
        self._ocs_consumer.run(self.on_ocs_message)

    def run_ack_consumer(self, threadname, delay):
        self._ack_consumer.run(self.on_ack_message)



    def setup_publishers(self):
        LOGGER.info('Setting up Base publisher on %s', self._base_broker_url)
        LOGGER.info('Setting up NCSA publisher on %s', self._ncsa_broker_url)
        self._base_publisher = SimplePublisher(self._base_broker_url)
        self._ncsa_publisher = SimplePublisher(self._ncsa_broker_url)




    def on_ocs_message(self, ch, method, properties, body):
        #msg_dict = yaml.load(body) 
        msg_dict = body 
        LOGGER.info('In DMCS message callback')
        LOGGER.debug('Thread in DMCS callback is %s', thread.get_ident())
        LOGGER.info('Message from DMCS callback message body is: %s', str(msg_dict))

        handler = self._msg_actions.get(msg_dict[MSG_TYPE])
        result = handler(msg_dict)
    
    def on_foreman_message(self, ch, method, properties, body):
        #msg_dict = yaml.load(body) 
        msg_dict = body 
        LOGGER.info('In DMCS message callback')
        LOGGER.debug('Thread in DMCS callback is %s', thread.get_ident())
        LOGGER.info('Message from DMCS callback message body is: %s', str(msg_dict))

        handler = self._msg_actions.get(msg_dict[MSG_TYPE])
        result = handler(msg_dict)
    

    def on_ack_message(self, ch, method, properties, body):
        msg_dict = body 
        LOGGER.info('In ACK message callback')
        LOGGER.debug('Thread in ACK callback is %s', thread.get_ident())
        LOGGER.info('Message from ACK callback message body is: %s', str(msg_dict))

        handler = self._msg_actions.get(msg_dict[MSG_TYPE])
        result = handler(msg_dict)
   ==================================================================================== 



    def process_ack(self, params):
        self.ACK_SCBD.add_timed_ack(params)
        
    def increment_job_num(self):
       self._current_job = str(self._session_id) + "_" + str(self.JOB_SCBD.get_next_job_num())


    def intake_yaml_file(self, filename):
        """This method reads the ForemanCfg.yaml config file
           found in the same directory as the BaseForeman class.
           The config file can list an initial set of forwarders and/or
           distributors, as well as the message broker address, default 
           file transfer values, etc.
    
        """
        try:
            f = open(filename)
        except IOError:
            print "Cant open %s" % filename
            print "Bailing out..."
            sys.exit(99)

        #cfg data map...
        cdm = yaml.safe_load(f)

        f.close()

        return cdm


    def export_yaml_file(self, filename, params):
        try:
            f = open(filename, 'w')
        except IOError:
            print "Cant open %s" % filename
            print "No YAML File Exported to %s" % filename"

        f.write(yaml.dump(params))

        f.close()

        return cdm


    def get_next_timed_ack_id(self, ack_type):
        self._next_timed_ack_id = self._next_timed_ack_id + 1
        retval = ack_type + "_" + str(self._next_timed_ack_id).zfill(6)
        return retval 


    def ack_timer(self, seconds):
        sleep(seconds)
        return True

    def purge_broker(self):
        #This will either move to an external script, or be done dynamically by reading cfg file
        os.system('rabbitmqctl -p /tester purge_queue f_consume')
        os.system('rabbitmqctl -p /tester purge_queue forwarder_publish')
        os.system('rabbitmqctl -p /tester purge_queue ack_publish')
        os.system('rabbitmqctl -p /tester purge_queue dmcs_consume')
        os.system('rabbitmqctl -p /tester purge_queue ncsa_consume')

        os.system('rabbitmqctl -p /bunny purge_queue forwarder_publish')
        os.system('rabbitmqctl -p /bunny purge_queue ack_publish')
        os.system('rabbitmqctl -p /bunny purge_queue F1_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F2_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F3_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F4_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F5_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F6_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F7_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F8_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F9_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F10_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F11_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F12_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F13_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F14_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F15_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F16_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F17_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F18_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F19_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F20_consume')
        os.system('rabbitmqctl -p /bunny purge_queue F21_consume')


def main():
    logging.basicConfig(filename='logs/BaseForeman.log', level=logging.INFO, format=LOG_FORMAT)
    b_fm = BaseForeman()
    print "Beginning BaseForeman event loop..."
    try:
        while 1:
            pass
    except KeyboardInterrupt:
        pass

    print ""
    print "Base Foreman Done."


if __name__ == "__main__": main()