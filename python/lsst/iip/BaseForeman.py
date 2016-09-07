import toolsmod
from toolsmod import get_timestamp
import logging
import pika
import redis
import yaml
import sys
import os
import time
import thread
from const import *
from Scoreboard import Scoreboard
from ForwarderScoreboard import ForwarderScoreboard
from JobScoreboard import JobScoreboard
from Consumer import Consumer
from SimplePublisher import SimplePublisher

LOG_FORMAT = ('%(levelname) -10s %(asctime)s %(name) -30s %(funcName) '
              '-35s %(lineno) -5d: %(message)s')
LOGGER = logging.getLogger(__name__)


class BaseForeman:
    FWD_SCBD = None
    JOB_SCBD = None
    ACK_SCBD = None
    DMCS_PUBLISH = "dmcs_publish"
    DMCS_CONSUME = "dmcs_consume"
    NCSA_PUBLISH = "ncsa_publish"
    FORWARDER_PUBLISH = "forwarder_publish"
    ACK_PUBLISH = "ack_publish"
    EXCHANGE = 'message'
    EXCHANGE_TYPE = 'direct'


    def __init__(self):
        toolsmod.singleton(self)

        self._name = 'FM'      # Message broker user & passwd
        self._passwd = 'FM'   
        self._base_broker_url = 'amqp_url'
        self._ncsa_broker_url = 'amqp_url'
        self._next_timed_ack_id = 0
 
        cdm = self.intake_yaml_file()
        try:
            self._base_broker_addr = cdm[ROOT][BROKER_ADDR]
            self._ncsa_broker_addr = cdm[ROOT][NCSA_BROKER_ADDR]
            forwarder_dict = cdm[ROOT][XFER_COMPONENTS][FORWARDERS]
        except KeyError as e:
            print "Dictionary error"
            print "Bailing out..."
            sys.exit(99)

        # Create Redis Forwarder table with Forwarder info

        self.FWD_SCBD = ForwarderScoreboard(forwarder_dict)
        self.JOB_SCBD = JobScoreboard()
        self.ACK_SCBD = AckScoreboard()
        self._msg_actions = { 'NEW_JOB': self.process_dmcs_new_job,
                              'STANDBY': self.process_dmcs_standby,
                              'READOUT': self.process_dmcs_readout,
                              'NCSA_RESOURCES_QUERY_ACK': self.process_ack,
                              'NCSA_STANDBY_ACK': self.process_ack,
                              'NCSA_READOUT_ACK': self.process_ack,
                              'FORWARDER_HEALTH_ACK': self.process_ack,
                              'FORWARDER_STANDBY_ACK': self.process_ack,
                              'FORWARDER_READOUT_ACK': self.process_ack }


        self._base_broker_url = "amqp://" + self._name + ":" + self._passwd + "@" + str(self._base_broker_addr)
        LOGGER.info('Building _base_broker_url. Result is %s', self._base_broker_url)

        self.setup_publishers()
        self.setup_consumers()

        self._ncsa_broker_url = "" 
        self.setup_federated_exchange()


    def setup_consumers(self):
        """This method sets up a message listener from each entity
           with which the BaseForeman has contact here. These
           listeners are instanced in this class, but their run
           methods are each called as a separate thread. While
           pika does not claim to be thread safe, the manner in which 
           the listeners are invoked below is a safe implementation
           that provides non-blocking, fully asynchronous messaging
           to the BaseForeman.

           The code in this file expects message bodies to arrive as
           YAML'd python dicts, while in fact, message bodies are sent
           on the wire as XML; this way message format can be validated,
           versioned, and specified in just one place. To make this work,
           there is an object that translates the params dict to XML, and
           visa versa. The translation object is instantiated by the consumer
           and acts as a filter before sending messages on to the registered
           callback for processing.

        """
        LOGGER.info('Setting up consumers on %s', self._base_broker_url)
        LOGGER.info('Running start_new_thread on all consumer methods')

        self._dmcs_consumer = Consumer(self._base_broker_url, self.DMCS_PUBLISH)
        try:
            thread.start_new_thread( self.run_dmcs_consumer, ("thread-dmcs-consumer", 2,) )
        except:
            LOGGER.critical('Cannot start DMCS consumer thread, exiting...')
            sys.exit(99)

        self._forwarder_consumer = Consumer(self._base_broker_url, self.FORWARDER_PUBLISH)
        try:
            thread.start_new_thread( self.run_forwarder_consumer, ("thread-forwarder-consumer", 2,) )
        except:
            LOGGER.critical('Cannot start FORWARDERS consumer thread, exiting...')
            sys.exit(100)

        self._ncsa_consumer = Consumer(self._base_broker_url, self.NCSA_PUBLISH)
        try:
            thread.start_new_thread( self.run_ncsa_consumer, ("thread-ncsa-consumer", 2,) )
        except:
            LOGGER.critical('Cannot start NCSA consumer thread, exiting...')
            sys.exit(101)

        self._ack_consumer = Consumer(self._base_broker_url, self.ACK_PUBLISH)
        try:
            thread.start_new_thread( self.run_ack_consumer, ("thread-ack-consumer", 2,) )
        except:
            LOGGER.critical('Cannot start ACK consumer thread, exiting...')
            sys.exit(102)

        LOGGER.info('Finished starting all three consumer threads')


    def run_dmcs_consumer(self, threadname, delay):
        self._dmcs_consumer.run(self.on_dmcs_message)


    def run_forwarder_consumer(self, threadname, delay):
        self._forwarder_consumer.run(self.on_forwarder_message)


    def run_ncsa_consumer(self, threadname, delay):
        self._ncsa_consumer.run(self.on_ncsa_message)

    def run_ack_consumer(self, threadname, delay):
        self._ack_consumer.run(self.on_ack_message)



    def setup_publishers(self):
        LOGGER.info('Setting up Base publisher on %s', self._base__broker_url)
        LOGGER.info('Setting up NCSA publisher on %s', self._ncsa_broker_url)

        self._publisher = SimplePublisher(self._base_broker_url)
        self._ncsa_publisher = SimplePublisher(self._ncsa_broker_url)
        #self._publisher.run() 


    def setup_federated_exchange(self):
        # Set up connection URL for NCSA Broker here.
        self._ncsa_broker_url = "amqp://" + self._name + ":" + self._passwd + "@" + str(self._ncsa_broker_addr)
        LOGGER.info('Building _ncsa_broker_url. Result is %s', self._ncsa_broker_url)
        pass


    def on_dmcs_message(self, ch, method, properties, body):
        msg_dict = yaml.load(body) 
        LOGGER.info('In DMCS message callback')
        LOGGER.debug('Thread in DMCS callback is %s', thread.get_ident())
        LOGGER.info('Message from DMCS callback message body is: %s', str(msg_dict))

        handler = self._msg_actions.get(msg_dict[MSG_TYPE])
        result = handler(msg_dict)
    

    def on_forwarder_message(self, ch, method, properties, body):
        LOGGER.info('In Forwarder message callback, thread is %s', thread.get_ident())
        LOGGER.info('forwarder callback msg body is: %s', str(body))
        pass

    def on_ncsa_message(self,ch, method, properties, body):
        LOGGER.info('In ncsa message callback, thread is %s', thread.get_ident())
        msg_dict = yaml.load(body)
        LOGGER.info('ncsa msg callback body is: %s', str(msg_dict))

        handler = self._msg_actions.get(msg_dict[MSG_TYPE])
        result = handler(msg_dict)

    def on_ack_message(self, ch, method, properties, body):
        msg_dict = yaml.load(body) 
        LOGGER.info('In ACK message callback')
        LOGGER.debug('Thread in ACK callback is %s', thread.get_ident())
        LOGGER.info('Message from ACK callback message body is: %s', str(msg_dict))

        handler = self._msg_actions.get(msg_dict[MSG_TYPE])
        result = handler(msg_dict)
    

    def process_dmcs_new_job(self, params):
        job_num = str(params[JOB_NUM])
        raft_list = params[RAFTS]
        needed_workers = len(raft_list)
        self.JOB_SCBD.add_job(job_num, needed_workers)
        LOGGER.info('Received new job %s. Needed workers is %s', job_num, needed_workers)

        # run forwarder health check
        # get timed_ack_id
        timed_ack = self.get_next_timed_ack_id("Forwarder_Ack")

        forwarders = self.FWD_SCBD.return_forwarders_list()
        # Mark all healthy Forwarders Unknown
        state_status = {"STATE": "HEALTH_CHECK", "STATUS": "UNKNOWN"}
        self.FWD_SCBD.set_forwarder_params(healthy_forwarders, state_status)
        # send health check messages
        ack_params = {}
        ack_params[MSG_TYPE] = "HEALTH_CHECK"
        ack_params["ACK_ID"] = timed_ack
        ack_params[JOB_NUM] = job_num
        for forwarder in forwarders:
            self._publisher.publish_message(self.FWD_SCBD.get_value_for_forwarder(forwarder,"CONSUME_QUEUE"),
                                            yaml.dump(ack_params))
        
        # start timer
        self.ack_timer(7)  # This is a HUGE number seconds for now...final setting will be milliseconds
        # at end of timer, get list of forwarders
        healthy_forwarders = self.ACK_SCBD.get_components_for_timed_ack(timed_ack)
        # update Forwarder scoreboard with healthy forwarders
        healthy_status = {"STATUS": "HEALTHY"}
        self.FWD_SCBD.set_forwarder_params(healthy_forwarders, healthy_status)
        #for fwder in healthy_forwarders:
        #    self.FWD_SCBD.set_forwarder_status(fwder, "HEALTHY")


        num_healthy_forwarders = len(healthy_forwarders)
        if needed_workers > num_healthy_forwarders:
            # send response msg to dmcs refusing job
            LOGGER.info('Reporting to DMCS that there are insufficient healthy forwarders for job #%s', job_num)
            dmcs_params = {}
            dmcs_params[MSG_TYPE] = NEW_JOB_ACK
            dmcs_params[JOB_NUM] = job_num
            dmcs_params[ACK_BOOL] = False
            dmcs_params[NEEDED_FORWARDERS] = str(needed_workers)
            dmcs_params[AVAILABLE_FORWARDERS] = str(num_healthy_forwarders)
            self._publisher.publish_message("dmcs_consume", yaml.dump(dmcs_params))
            # delete job and leave Forwarders in Idle state
            self.JOB_SCBD.delete_job(job_num)
            idle_state = {"STATE": "IDLE"}
            self.FWD_SCBD.set_forwarder_params(healthy_forwarders, idle_state)
            return False
        else:
            LOGGER.info('Sufficient forwarders have been found. Checking NCSA')
            self._pairs_dict = {}
            forwarder_candidate_dict = {}
            for i in range (0, needed_workers):
                forwarder_candidate_dict[healthy_forwarders[i]] = raft_list[i]
                self.FWD_SCBD.set_forwarder_status(healthy_forwarders[i], NCSA_RESOURCES_QUERY)
                # Call this method for testing...
                # There should be a message sent to NCSA here asking for available resources
            timed_ack_id = self.get_next_timed_ack_id("NCSA_Ack") 
            ncsa_params = {}
            ncsa_params[MSG_TYPE] = "NCSA_RESOURCES_QUERY"
            ncsa_params[JOB_NUM] = job_num
            ncsa_params[RAFT_NUM] = needed_workers
            ncsa_params["ACK_ID"] = timed_ack_id
            ncsa_params["FORWARDERS"] = forwarder_candidate_dict
            self._ncsa_publisher.publish_message("ncsa_consume", yaml.dump(ncsa_params)) 
            LOGGER.info('The following forwarders have been sent to NCSA for pairing:')
            LOGGER.info(forwarder_candidate_dict)

            self.ack_timer(3)

            #Check ACK scoreboard for response from NCSA
            ncsa_response = self.ACK_SCBD.get_components_for_timed_ack(timed_ack_id)
            if ncsa_response:
                try:
                    ack_bool = ncsa_response[ACK_BOOL]
                    if ack_bool == True:
                        pairs = ncsa_response[PAIRS] 
                except KeyError, e:
                    pass 
                if ack_bool == TRUE:
                    #ncsa has enough resources...
                    self.JOB_SCBD.set_pairs_for_job(job_num, pairs)          
                    LOGGER.info('The following pairs will be used for Job #%s: %s',
                                 job_num, str(self._pairing_dict))
                    fwd_ack_id = self.get_next_timed_ack_id("FWD_PARAMS_ACK")
                    fwders = pairs.keys()
                    fwd_params = {}
                    fwd_params[MSG_TYPE] = "FORWARDER_JOB_PARAMS"
                    fwd_params[JOB_NUM] = job_num
                    fwd_params[ACK_ID] = fwd_ack_id
                    for fwder in fwders:
                        fwd_params["TRANSFER_PARAMS"] = pairs[fwder]
                        route_key = self.FWD_SCBD.get_value_for_forwarder(fwder, "CONSUME_QUEUE")
                        self._publisher.publish_message(route_key, yaml.dump(fwd_params))
                    in_ready_state = {'STATE':'IN_READY_STATE'}
                    self.FWD_SCBD.set_forwarder_params(fwders, in_ready_state) 

                    self.ack_timer(3)

                    fwd_params_response = self.ACK_SCBD.get_components_for_timed_ack(fwd_ack_id)
                    if fwd_params_response and len(fwd_params_response):
                        # Tell DMCS we are ready
                        dmcs_message = {}
                        dmcs_message[JOB_NUM] = job_num
                        dmcs_message[MSG_TYPE] = NEW_JOB_ACK
                        dmcs_message[ACK_BOOL] = True
                        self._publisher.publish_message("dmcs_consume", yaml.dump(dmcs_message))
                else:
                    #not enough ncsa resources to do job - Notify DMCS
                    dmcs_params = {}
                    dmcs_params[MSG_TYPE] = "NEW_JOB_ACK"
                    dmcs_params[JOB_NUM] = job_num 
                    dmcs_params[ACK_BOOL] = False
                    dmcs_params[BASE_RESOURCES] = True
                    dmcs_params[NCSA_RESOURCES] = False
                    dmcs_params[NEEDED_DISTRIBUTORS] = ncsa_response[NEEDED_DISTRIBUTORS]
                    dmcs_params[AVAILABLE_DISTRIBUTORS] = ncsa_response[AVAILABLE_DISTRIBUTORS]
                    dmcs_params[NEEDED_WORKERS] = ncsa_response[NEEDED_WORKERS]
                    dmcs_params[AVAILABLE_WORKERS] = ncsa_response[AVAILABLE_WORKERS]
                    self._publisher.publish_message("dmcs_consume", yaml.dump(dmcs_message) )
                    idle_param = {'STATE': 'IDLE'}
                    self.FWD_SCBD.set_forwarder_params(forwarder_candidate_dict.keys(), idle_params)
            else:
                #No answer from NCSA...
                dmcs_params = {}
                dmcs_params[MSG_TYPE] = "NEW_JOB_ACK"
                dmcs_params[JOB_NUM] = job_num 
                dmcs_params[ACK_BOOL] = False
                dmcs_params[BASE_RESOURCES] = True
                dmcs_params[NCSA_RESOURCES] = False
                dmcs_params[NEEDED_DISTRIBUTORS] = ncsa_response[NEEDED_DISTRIBUTORS]
                dmcs_params[AVAILABLE_DISTRIBUTORS] = str(0)
                dmcs_params[NEEDED_WORKERS] = ncsa_response[NEEDED_WORKERS]
                dmcs_params[AVAILABLE_WORKERS] = str(0)
                self._publisher.publish_message("dmcs_consume", yaml.dump(dmcs_params) )
                idle_param = {'STATE': 'IDLE'}
                self.FWD_SCBD.set_forwarder_params(forwarder_candidate_dict.keys(), idle_params)
                     

    """
    def process_dmcs_standby(self, params):
        # tell all forwarders then distributors
        job_num = params[JOB_NUM]
        pairs = self.JOB_SCBD.get_pairs_for_job(str(job_num))

        forwarders = pairs.keys()
        fwd_ack_id = self.get_next_timed_ack_id('FORWARDER_STANDBY')
        fwd_params = {}
        fwd_params[STATE] = STANDBY 
        for forwarder in forwarders:
            fwd_params = {}
            fwd_params[JOB_NUM] = job_num
            fwd_params[PAIRING] = pairs[forwarder]
            fwd_params[MSG_TYPE] = FORWARDER_STANDBY 
            fwd_params[ACK_ID] = fwd_ack_id
            routing_key = self.FWD_SCBD.get_value_for_forwarder(forwarder, ROUTING_KEY)
            LOGGER.info('Using routing key %s for forwarder %s message. Msg is %s',
                         routing_key, forwarder, msg_params)
            self._publisher.publish_message(routing_key, yaml.dump(fwd_params))
        self.FWD_SCBD.set_forwarder_params(forwarder, fwd_params)

        ncsa_ack_id = self.get_next_timed_ack_id('NCSA_STANDBY')
        ncsa_params = {}
        ncsa_params[JOB_NUM] = job_num
        ncsa_params[MSG_TYPE] = 'NCSA_STANDBY'
        ncsa_params['ACK_ID'] = ncsa_ack_id
        self._publisher.publish_message('ncsa_consume', yaml.dump(ncsa_params))
        

        self.ack_timer(4)
        ncsa_response = self.ACK_SCBD.get_components_for_timed_ack(ncsa_ack_id)
        if ncsa_response:
            if ncsa_response['ACK_BOOL'] == True:
             ##################XXXXXXXXXXXXXXXXXXXXXXXXXX#############################
    """         


    def process_dmcs_readout(self, params):
        job_number = params[JOB_NUM]
        pairs = self.JOB_SCBD.get_pairs_for_job(job_number)
        date - get_timestamp()
        self.JOB_SCBD.set_value_for_job(job_number, START_READOUT, date) 
        # The following line extracts the distributor FQNs from pairs dict using 
        # list comprehension values; faster than for loops
        distributors = [v['FQN'] for v in pairs.values()]
        forwarders = pairs.keys()

        ack_id = self.get_next_timed_ack_id('NCSA_READOUT')
### Send READOUT to NCSA with ACK_ID
        ncsa_params = {}
        ncsa_params[MSG_TYPE] = 'NCSA_READOUT'
        ncsa_params['ACK_ID'] = ack_id

        self.ack_timer(4)

        ncsa_response = self.ACK_SCBD.get_components_for_timed_ack(ack_id)
        if ncsa_response:
            if ncsa_response['ACK_BOOL'] == True:
                #inform forwarders
                fwd_ack_id = self.get_next_timed_ack_id('FORWARDER_READOUT')
                for forwarder in forwarders:
                    name = self.FWD_SCBD.get_value_for_forwarder(forwarder, NAME)
                    routing_key = self.FWD_SCBD.get_routing_key(forwarder)
                    msg_params = {}
                    msg_params[MSG_TYPE] = 'FORWARDER_READOUT'
                    msg_params[JOB_NUM] = job_number
                    msg_params['ACK_ID'] = fwd_ack_id
                    self.FWD_SCBD.set_forwarder_state(forwarder, START_READOUT)
                    self._publisher.publish_message(routing_key, yaml.dump(msg_params))
                self.ack_timer(4)
                forwarder_responses = self.ACK_SCBD.get_components_for_timed_ack(fwd_ack_id)
                if len(forwarder_responses) == len(forwarders):
                    dmcs_params = {}
                    dmcs_params[MSG_TYPE] = 'READOUT_ACK' 
                    dmcs_params[JOB_NUM] = job_number
                    dmcs_params['ACK_BOOL'] = True
                    dmcs_params['COMMENT'] = "Readout begun at %s" % get_timestamp()
                    self._publisher.publish_message('dmcs_consume', yaml.dump(dmcs_params))
                    
            else:
                #send problem with ncsa to DMCS
                dmcs_params = {}
                dmcs_params[MSG_TYPE] = 'READOUT_ACK' 
                dmcs_params[JOB_NUM] = job_number
                dmcs_params['ACK_BOOL'] = False
                dmcs_params['COMMENT'] = 'Readout Failed: Problem at NCSA - Expected Distributor Acks is %s, Number of Distributor Acks received is %s' % (ncsa_response['EXPECTED_DISTRIBUTOR_ACKS'], ncsa_response['RECEIVED_DISTRIBUTOR_ACKS'])
                self._publisher.publish_message('dmcs_consume', yaml.dump(dmcs_params))
                    
        else:
            #send 'no response from ncsa' to DMCS               )
            dmcs_params = {}
            dmcs_params[MSG_TYPE] = 'READOUT_ACK' 
            dmcs_params[JOB_NUM] = job_number
            dmcs_params['ACK_BOOL'] = False
            dmcs_params['COMMENT'] = "Readout Failed: No Response from NCSA"
            self._publisher.publish_message('dmcs_consume', yaml.dump(dmcs_params))
                    
        

    def process_ack(self, params):
        self.ACK_SCBD.add_timed_ack(params)
        

    def intake_yaml_file(self):
        """This method reads the ForemanCfg.yaml config file
           found in the same directory as the BaseForeman class.
           The config file can list an initial set of forwarders and/or
           distributors, as well as the message broker address, default 
           file transfer values, etc.
    
        """
        try:
            f = open('ForemanCfg.yaml')
        except IOError:
            print "Cant open ForemanCfg.yaml"
            print "Bailing out..."
            sys.exit(99)

        #cfg data map...
        cdm = yaml.safe_load(f)

        try:
            self._xfer_app = cdm[XFER_APP]
        except KeyError as e:
            pass #use default or await reassignment

        try:
            self._xfer_file = cdm[XFER_FILE]
        except KeyError:
            pass #use default or await reassignment

        return cdm


    def get_next_timed_ack_id(self, ack_type):
        self._next_timed_ack_id = self._next_timed_ack_id + 1
        retval = ack_type + "_" + str(self._next_timed_ack_id).zfill(6)
        return retval 


    def ack_timer(self, seconds):
        sleep(seconds)
        return True


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
