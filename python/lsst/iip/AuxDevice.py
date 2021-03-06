### FIX MOVE NEW_ARCHIVE_ITEM message publish to NEW_VISIT/NEW_TARGET and remove unneeded params from message body.
### JOB SCOREBOARD should store te ARCHIVE file destination/path and resend to each forwarder for readout.

import toolsmod
from toolsmod import get_timestamp
import logging
import pika
import redis
import yaml
import sys
import os
from copy import deepcopy
import pprint
import time
from time import sleep
import threading
from const import *
from Scoreboard import Scoreboard
from ForwarderScoreboard import ForwarderScoreboard
from JobScoreboard import JobScoreboard
from AckScoreboard import AckScoreboard
from Consumer import Consumer
from ThreadManager import ThreadManager
from SimplePublisher import SimplePublisher

LOG_FORMAT = ('%(levelname) -10s %(asctime)s %(name) -30s %(funcName) -35s %(lineno) -5d: %(message)s')
LOGGER = logging.getLogger(__name__)


class AuxDevice:
    """ The Spec Device is a commandable device which coordinates the ingest of
        images from the telescope camera and then the transfer of those images to
        the base site archive storage.
        It receives jobs and divides and assigns the work to forwarders, records state and
        status change of forwarders, and sends messages accordingly.
    """
    COMPONENT_NAME = 'AUX_FOREMAN'
    AT_FOREMAN_CONSUME = "at_foreman_consume"
    ARCHIVE_CTRL_PUBLISH = "archive_ctrl_publish"
    ARCHIVE_CTRL_CONSUME = "archive_ctrl_consume"
    AT_FOREMAN_ACK_PUBLISH = "at_foreman_ack_publish"
    START_INTEGRATION_XFER_PARAMS = {}
    ACK_QUEUE = []
    CFG_FILE = 'L1SystemCfg.yaml'
    prp = toolsmod.prp
    DP = toolsmod.DP
    RAFT_LIST = []
    RAFT_CCD_LIST = ['00']


    def __init__(self, filename=None):
        """ Create a new instance of the Spectrograph Device class.
            Instantiate the instance, raise assertion error if already instantiated.
            Extract config values from yaml file.
            Store handler methods for each message type.
            Set up base broker url, publishers, and scoreboards. Consumer threads
            are started within a Thread Manager object so that they can be monitored
            for health and shutdown/joined cleanly when the app exits.

            :params filename: Deflaut 'L1SystemCfg.yaml'. Can be assigned by user.

            :return: None.
        """
        toolsmod.singleton(self)

        self._config_file = self.CFG_FILE
        if filename != None:
            self._config_file = filename

        LOGGER.info('Extracting values from Config dictionary')
        self.extract_config_values()


        #self.purge_broker(cdm['ROOT']['QUEUE_PURGES'])



        self._msg_actions = { 'AT_START_INTEGRATION': self.process_at_start_integration,
                              'AT_NEW_SESSION': self.set_session,
                              #'AR_READOUT': self.process_dmcs_readout,
                              'AUX_FWDR_HEALTH_CHECK_ACK': self.process_ack,
                              'AUX_FWDR_XFER_PARAMS_ACK': self.process_ack,
                              'AR_FWDR_READOUT_ACK': self.process_ack,
                              'AR_ITEMS_XFERD_ACK': self.process_ack,
                              'AT_HEADER_READY': self.process_header_ready_event,
                              'NEW_ARCHIVE_ITEM_ACK': self.process_ack, 
                              #'AUX_TAKE_IMAGES': self.take_images,
                              'AT_END_READOUT': self.process_at_end_readout }


        self._next_timed_ack_id = 0

        self.setup_publishers()

        LOGGER.info('ar foreman consumer setup')
        self.thread_manager = None
        self.setup_consumer_threads()

        LOGGER.info('Archive Foreman Init complete')


    def setup_publishers(self):
        """ Set up base publisher with pub_base_broker_url by creating a new instance
            of SimplePublisher class with yaml format

            :params: None.

            :return: None.
        """
        self.pub_base_broker_url = "amqp://" + self._msg_pub_name + ":" + \
                                            self._msg_pub_passwd + "@" + \
                                            str(self._base_broker_addr)
        LOGGER.info('Setting up Base publisher on %s using %s', self.pub_base_broker_url, self._base_msg_format)
        self._publisher = SimplePublisher(self.pub_base_broker_url, self._base_msg_format)


    def on_aux_foreman_message(self, ch, method, properties, body):
        """ Calls the appropriate AR message action handler according to message type.

            :params ch: Channel to message broker, unused unless testing.
            :params method: Delivery method from Pika, unused unless testing.
            :params properties: Properties from DMCS to AR Foreman callback message
                                body, unused unless testing.
            :params body: A dictionary that stores the message body.

            :return: None.
        """
        #msg_dict = yaml.load(body) 
        ch.basic_ack(method.delivery_tag)
        msg_dict = body 
        LOGGER.info('In AUX Foreman message callback')
        LOGGER.info('Message from DMCS to AUX Foreman callback message body is: %s', str(msg_dict))
        print("Incoming AUX msg is: %s" % msg_dict)
        handler = self._msg_actions.get(msg_dict[MSG_TYPE])
        result = handler(msg_dict)
    

    def on_archive_message(self, ch, method, properties, body):
        """ Calls the appropriate AR message action handler according to message type.

            :params ch: Channel to message broker, unused unless testing.
            :params method: Delivery method from Pika, unused unless testing.
            :params properties: Properties from AR CTRL callback message body,
                                unused unless testing.
            :params body: A dictionary that stores the message body.

            :return: None.
        """
        ch.basic_ack(method.delivery_tag)
        LOGGER.info('AR CTRL callback msg body is: %s', str(body))

        handler = self._msg_actions.get(msg_dict[MSG_TYPE])
        result = handler(msg_dict)

    def on_ack_message(self, ch, method, properties, body):
        """ Calls the appropriate AR message action handler according to message type.

            :params ch: Channel to message broker, unused unless testing.
            :params method: Delivery method from Pika, unused unless testing.
            :params properties: Properties from ACK callback message body, unused
                                unless testing.
            :params body: A dictionary that stores the message body.

            :return: None.
        """
        ch.basic_ack(method.delivery_tag) 
        msg_dict = body 
        print("")
        print("")
        print("")
        print( "RECEIVING ack MESSAGE:")
        print(msg_dict)
        print("")
        print("")
        print("")

        # XXX FIX Ignoring all log messages
        return

        LOGGER.info('In ACK message callback')
        LOGGER.info('Message from ACK callback message body is: %s', str(msg_dict))

        # XXX FIX Ignoring all log messages
        return



        handler = self._msg_actions.get(msg_dict[MSG_TYPE])
        result = handler(msg_dict)
    


    def process_at_start_integration(self, params):
        # When this method is invoked, the following must happen:
        #    1) Health check all forwarders
        #    2) Divide work and generate dict of forwarders and which rafts/ccds they are fetching
        #    3) Inform each forwarder which rafts they are responsible for
        # receive new job_number and image_id; session and visit are current
        # and deep copy it with some additions such as session and visit
        # These next three lines must have WFS and Guide sensor info added
        start_int_ack_id = params[ACK_ID]

        print("Incoming AUX AT_Start Int msg")
        # next, run health check
        self.ACK_QUEUE = {}
        health_check_ack_id = self.get_next_timed_ack_id('AUX_FWDR_HEALTH_ACK')
        num_fwdrs_checked = self.fwdr_health_check(health_check_ack_id)

        # Add job scbd entry
        self.ack_timer(1.4)

        #healthy_fwdrs = self.ACK_QUEUE.get_components_for_timed_ack(health_check_ack_id)
        #if healthy_fwdrs == None:
        #    self.refuse_job(params, "No forwarders available")
        #    ### FIX send error code for this...
        #    return

        fwdr_names = list(self._forwarder_dict.keys())
        self._current_fwdr = self._forwarder_dict[fwdr_names[0]]

        # Add archive check when necessary...
        # send new_archive_item msg to archive controller
        #start_int_params = {}
        #ac_timed_ack = self.get_next_timed_ack_id('AUX_CTRL_NEW_ITEM')
        #start_int_params[MSG_TYPE] = 'NEW_ARCHIVE_ITEM'
        #start_int_params['ACK_ID'] = ac_timed_ack
        #start_int_params['JOB_NUM'] = job_number
        #start_int_params['SESSION_ID'] = session_id
        #start_int_params['VISIT_ID'] = visit_id
        #start_int_params['IMAGE_ID'] = image_id
        #start_int_params['REPLY_QUEUE'] = self.AUX_FOREMAN_ACK_PUBLISH
        #self.JOB_SCBD.set_job_state(job_number, 'AR_NEW_ITEM_QUERY')
        #self._publisher.publish_message(self.ARCHIVE_CTRL_CONSUME, start_int_params)

        #ar_response = self.progressive_ack_timer(ac_timed_ack, 1, 2.0)

        #if ar_response == None:
        #   FIXME raise L1 exception and bail out
        #   print("B-B-BAD Trouble; no ar_response")
           
       
        #target_dir = ar_response['ARCHIVE_CTRL']['TARGET_DIR']
        target_dir = self.archive_xfer_root 
        #self.JOB_SCBD.set_job_params(job_number, {'STATE':'AR_NEW_ITEM_RESPONSE', 'TARGET_DIR': dir})
        

        # divide image fetch across forwarders
        #list_of_fwdrs = list(healthy_fwdrs.keys())
        #work_schedule = self.divide_work(list_of_fwdrs, raft_list, raft_ccd_list)

        # send target dir, and job, session,visit and work to do to healthy forwarders
        #self.JOB_SCBD.set_value_for_job(job_number, 'STATE','SENDING_XFER_PARAMS')
        #set_sched_result = self.JOB_SCBD.set_work_schedule_for_job(job_number, work_schedule)
        #if set_sched_result == False:
            # FIXME Raise L1 exception and bail
        #    print("BIG PROBLEM - CANNOT SET WORK SCHED IN SCBD")
      

        xfer_params_ack_id = self.get_next_timed_ack_id("AT_FWDR_PARAMS_ACK") 

        fwdr_new_target_params = {} 
        fwdr_new_target_params['XFER_PARAMS'] = {}
        fwdr_new_target_params[MSG_TYPE] = 'AT_FWDR_XFER_PARAMS'
        #fwdr_new_target_params[SESSION_ID] = session_id
        fwdr_new_target_params[IMAGE_ID] = params[IMAGE_ID]
        fwdr_new_target_params['IMAGE_INDEX'] = params['IMAGE_INDEX']
        #fwdr_new_target_params[VISIT_ID] = visit_id
        #fwdr_new_target_params[JOB_NUM] = job_number
        fwdr_new_target_params[ACK_ID] = xfer_params_ack_id
        fwdr_new_target_params[REPLY_QUEUE] = self.AT_FOREMAN_ACK_PUBLISH
        target_location = self.archive_name + "@" + self.archive_ip + ":" + target_dir
        fwdr_new_target_params['TARGET_LOCATION'] = target_location

        xfer_params_dict = {}
        xfer_params_dict['RAFT_LIST'] = self._wfs_raft
        #xfer_params_dict['RAFT_LIST'] = self.RAFT_LIST
        #xfer_params_dict['RAFT_LIST'].append(self.RAFT_LIST)
        #xfer_params_dict['RAFT_CCD_LIST'] = []
        #xfer_params_dict['RAFT_CCD_LIST'].append(self.RAFT_CCD_LIST)
        xfer_params_dict['AT_FWDR'] = self._current_fwdr
        fwdr_new_target_params['XFER_PARAMS'] = xfer_params_dict
        route_key = self._current_fwdr["CONSUME_QUEUE"]
        self._publisher.publish_message(route_key, fwdr_new_target_params)
       

        
        """
        # receive ack back from forwarders that they have job params
        params_acks = self.progressive_ack_timer(xfer_params_ack_id, len_fwdrs_list, 3.0)

        ### FIX
        #   if params_acks == None:
        #     raise L1Exception and bail

        self.JOB_SCBD.set_value_for_job(job_number,'STATE','XFER_PARAMS_SENT')

        # accept job by Ach'ing True
        st_int_params_ack = {}
        st_int_params_ack['MSG_TYPE'] = 'AR_START_INTEGRATION_ACK'
        st_int_params_ack['ACK_ID'] = start_int_ack_id
        st_int_params_ack['ACK_BOOL'] = True
        st_int_params_ack['JOB_NUM'] = job_number
        st_int_params_ack['SESSION_ID'] = session_id
        st_int_params_ack['IMAGE_ID'] = image_id
        st_int_params_ack['VISIT_ID'] = visit_id
        st_int_params_ack['COMPONENT'] = self.COMPONENT_NAME
        self.accept_job(st_int_params_ack)

        self.JOB_SCBD.set_value_for_job(job_number, STATE, "JOB_ACCEPTED")
        fscbd_params = {'STATE':'AWAITING_READOUT'}
        self.FWD_SCBD.set_forwarder_params(healthy_fwdrs, fscbd_params)
        """

    def fwdr_health_check(self, ack_id):
        """ Send AR_FWDR_HEALTH_CHECK message to ar_foreman_ack_publish queue.
            Retrieve available forwarders from ForwarderScoreboard, set their state to
            HEALTH_CHECK, status to UNKNOWN, and publish the message.

            :params ack_id: Ack id for AR forwarder health check.

            :return: Number of health checks sent.
        """
        msg_params = {}
        msg_params[MSG_TYPE] = 'AT_FWDR_HEALTH_CHECK'
        msg_params[ACK_ID] = ack_id
        msg_params[REPLY_QUEUE] = self.AT_FOREMAN_ACK_PUBLISH

        forwarders = list(self._forwarder_dict.keys())
        for x in range (0, len(forwarders)):
            route_key = self._forwarder_dict[forwarders[x]]["CONSUME_QUEUE"]
            self._publisher.publish_message(route_key, msg_params)
        return len(forwarders)


    def divide_work(self, fwdrs_list, raft_list, raft_ccd_list):
        """ Divide work (ccds) among forwarders.

            If only one forwarder available, give it all the work.
            If have less or equal ccds then forwarders, give the first few forwarders one
            ccd each.
            Else, evenly distribute ccds among forwarders, and give extras to the first
            forwarder, make sure that ccd list for each forwarder is continuous.

            :params fwdrs_list: List of available forwarders for the job.
            :params ccd_list: List of ccds to be distributed.

            :return schedule: Distribution of ccds among forwarders.
        """
        num_fwdrs = len(fwdrs_list)
        num_rafts = len(raft_list)

        schedule = {}
        schedule['FORWARDER_LIST'] = []
        schedule['CCD_LIST'] = []  # A list of ccd lists; index of main list matches same forwarder list index
        FORWARDER_LIST = []
        RAFT_LIST = [] # This is a 'list of lists'
        RAFT_CCD_LIST = [] # This is a 'list of lists'
        if num_fwdrs == 1:
            FORWARDER_LIST.append(fwdrs_list[0])
            RAFT_LIST = deepcopy(raft_list)
            RAFT_CCD_LIST = deepcopy(raft_ccd_list)
            schedule['FORWARDER_LIST'] = FORWARDER_LIST
            schedule['RAFT_LIST'] = RAFT_LIST
            schedule['RAFT_CCD_LIST'] = RAFT_CCD_LIST
            return schedule

        if num_rafts <= num_fwdrs:
            for k in range (0, num_rafts):
                FORWARDER_LIST.append(fwdrs_list[k])
                #little_list.append(ccd_list[k])
                RAFT_LIST.append(raft_list[k])  # Need a copy here...
                RAFT_CCD_LIST.append = deepcopy(raft_ccd_list[k]) 
                schedule['FORWARDER_LIST'] = FORWARDER_LIST
                schedule['RAFT_LIST'] = RAFT_LIST
                schedule['RAFT_CCD_LIST'] = RAFT_CCD_LIST

        else:
            rafts_per_fwdr = len(raft_list) // num_fwdrs 
            remainder_rafts = len(raft_list) % num_fwdrs
            offset = 0
            for i in range(0, num_fwdrs):
                tmp_list = []
                tmp_raft_list = []
                for j in range (offset, (rafts_per_fwdr + offset)):
                    if (j) >= num_rafts:
                        break
                    tmp_list.append(raft_list[j])
                    tmp_raft_list.append(deepcopy(raft_ccd_list[j]))
                offset = offset + rafts_per_fwdr

                # If num_fwdrs divided into num_rafts equally, we are done...else, deal with remainder
                if remainder_rafts != 0 and i == 0:
                    for k in range(offset, offset + remainder_rafts):
                        tmp_list.append(raft_list[k])
                        tmp_raft_list.append(deepcopy(raft_ccd_list[k]))
                    offset = offset + remainder_rafts
                FORWARDER_LIST.append(fwdrs_list[i])
                RAFT_LIST.append(list(tmp_list))
                RAFT_CCD_LIST.append(list(tmp_raft_list))
            schedule['FORWARDER_LIST'] = FORWARDER_LIST
            schedule['RAFT_LIST'] = RAFT_LIST
            schedule['RAFT_CCD_LIST'] = RAFT_CCD_LIST

        return schedule


    def accept_job(self, dmcs_message):
        """ Send AR_START_INTEGRATION_ACK message with ack_bool equals True (job accepted)
            and other job specs to dmcs_ack_consume queue.

            :params dmcs_message: A dictionary that stores info of a job.

            :return: None.
        """
        self._publisher.publish_message("dmcs_ack_consume", dmcs_message)


    def refuse_job(self, params, fail_details):
        """ Send AR_START_INTEGRATION_ACK message with ack_bool equals False (job refused)
            and other job specs to dmcs_ack_consume queue.

            Set job state as JOB_REFUSED in JobScoreboard.

            :params parmas: A dictionary that stores info of a job.

            :params fail_details: A string that describes what went wrong, not used for now.

            :return: None.
        """
        dmcs_message = {}
        dmcs_message[JOB_NUM] = params[JOB_NUM]
        dmcs_message[MSG_TYPE] = 'AR_START_INTEGRATION_ACK'
        dmcs_message['ACK_ID'] = params['ACK_ID']
        dmcs_message['SESSION_ID'] = params['SESSION_ID']
        dmcs_message['VISIT_ID'] = params['VISIT_ID']
        dmcs_message['IMAGE_ID'] = params['IMAGE_ID']
        dmcs_message[ACK_BOOL] = False 
        dmcs_message['COMPONENT'] = self.COMPONENT_NAME
        self.JOB_SCBD.set_value_for_job(params[JOB_NUM], STATE, "JOB_REFUSED")
        self._publisher.publish_message("dmcs_ack_consume", dmcs_message)


    def process_at_end_readout(self, params):
        """ Set job state as PREPARE_READOUT in JobScoreboard.
            Send readout to forwarders.
            Set job state as READOUT_STARTED in JobScoreboard.
            Wait to retrieve and process readout responses.

            :params parmas: A dictionary that stores info of a job.

            :return: None.
        """
        print("Incoming AUX AT_END_READOUT msg")
        reply_queue = params['REPLY_QUEUE']
        readout_ack_id = params[ACK_ID]
        #job_number = params[JOB_NUM]
        image_id = params[IMAGE_ID]
        # send readout to forwarders
        #self.JOB_SCBD.set_value_for_job(job_number, 'STATE', 'READOUT')
        fwdr_readout_ack = self.get_next_timed_ack_id("AR_FWDR_READOUT_ACK")
        #work_schedule = self.JOB_SCBD.get_work_schedule_for_job(job_number)
        current_fwdr = self._current_fwdr
        msg = {}
        msg[MSG_TYPE] = 'AT_FWDR_END_READOUT'
        #msg[JOB_NUM] = job_number
        msg[IMAGE_ID] = image_id
        msg['IMAGE_INDEX'] = params['IMAGE_INDEX']
        route_key = self._current_fwdr['CONSUME_QUEUE']
        self._publisher.publish_message(route_key, msg)


        #readout_responses = self.progressive_ack_timer(fwdr_readout_ack, len(fwdrs), 4.0)

        # if readout_responses == None:
        #    raise L1 exception 

        #self.process_readout_responses(readout_ack_id, reply_queue, image_id, readout_responses)


    def process_readout_responses(self, readout_ack_id, reply_queue, image_id, readout_responses):
        """ From readout_responses param, retrieve image_id and job_number, and create list of
            ccd, filename, and checksum from all forwarders. Store into xfer_list_msg and
            send to archive to confirm each file made it intact.
            Send AR_READOUT_ACK message with results and ack_bool equals True to
            dmcs_ack_comsume queue.


            :params readout_ack_id: Ack id for AR_READOUT_ACK message.
            :params image_id:
            :params readout_responses: Readout responses from AckScoreboard.

            :return: None.
        """
        job_number = None
        image_id = None
        confirm_ack = self.get_next_timed_ack_id('AR_ITEMS_XFERD_ACK')
        fwdrs = list(readout_responses.keys())
        CCD_LIST = []
        FILENAME_LIST = []
        CHECKSUM_LIST = []
        for fwdr in fwdrs:
            ccds = readout_responses[fwdr]['RESULT_LIST']['CCD_LIST']
            num_ccds = len(ccds)
            fnames = readout_responses[fwdr]['RESULT_LIST']['FILENAME_LIST']
            csums = readout_responses[fwdr]['RESULT_LIST']['CHECKSUM_LIST']
            for i in range(0, num_ccds):
                msg = {}
                CCD_LIST.append(ccds[i])
                FILENAME_LIST.append(fnames[i])
                CHECKSUM_LIST.append(csums[i])
        job_number = readout_responses[fwdr][JOB_NUM]
        image_id = readout_responses[fwdr]['IMAGE_ID']
        xfer_list_msg = {}
        xfer_list_msg[MSG_TYPE] = 'AR_ITEMS_XFERD'
        xfer_list_msg[ACK_ID] = confirm_ack
        xfer_list_msg['IMAGE_ID'] = image_id
        xfer_list_msg['REPLY_QUEUE'] = self.AR_FOREMAN_ACK_PUBLISH
        xfer_list_msg['RESULT_LIST'] = {}
        xfer_list_msg['RESULT_LIST']['CCD_LIST'] = CCD_LIST
        xfer_list_msg['RESULT_LIST']['FILENAME_LIST'] = FILENAME_LIST
        xfer_list_msg['RESULT_LIST']['CHECKSUM_LIST'] = CHECKSUM_LIST
        self._publisher.publish_message(self.ARCHIVE_CTRL_CONSUME, xfer_list_msg) 
           
        xfer_check_responses = self.progressive_ack_timer(confirm_ack, 1, 4.0) 

        # if xfer_check_responses == None:
        #    raise L1 exception and bail

        results = xfer_check_responses['ARCHIVE_CTRL']['RESULT_LIST']

        ack_msg = {}
        ack_msg['MSG_TYPE'] = 'AR_READOUT_ACK'
        ack_msg['JOB_NUM'] = job_number
        ack_msg['COMPONENT'] = self.COMPONENT_NAME
        ack_msg['ACK_ID'] = readout_ack_id
        ack_msg['ACK_BOOL'] = True
        ack_msg['RESULT_LIST'] = results
        self._publisher.publish_message(reply_queue, ack_msg)

        ### FIXME Set state as complete for Job


                   
    def send_readout(self, params, fwdrs, readout_ack):
        """ Send AR_FWDR_READOUT message to each forwarder working on the job with
            ar_foreman_ack_publish queue as reply queue.

            :params params: A dictionary that stores info of a job.
            :params readout_ack: Ack id for AR_FWDR_READOUT message.

            :return: None.
        """
        ro_params = {}
        job_number = params['JOB_NUM']
        ro_params['MSG_TYPE'] = 'AR_FWDR_READOUT'
        ro_params['JOB_NUM'] = job_number
        ro_params['SESSION_ID'] = self.get_current_session()
        ro_params['VISIT_ID'] = self.get_current_visit()
        ro_params['IMAGE_ID'] = params['IMAGE_ID']
        ro_params['ACK_ID'] = readout_ack
        ro_params['REPLY_QUEUE'] = self.AR_FOREMAN_ACK_PUBLISH 
        for fwdr in fwdrs:
            route_key = self.FWD_SCBD.get_value_for_forwarder(fwdr, "CONSUME_QUEUE")
            self._publisher.publish_message(route_key, ro_params)

    def process_header_ready_event(self, params):
        fname = params['FILENAME']
        image_id = params['IMAGE_ID']
        msg = {}
        msg['MSG_TYPE'] = 'AT_FWDR_HEADER_READY'
        msg['FILENAME'] = fname
        msg['IMAGE_ID'] = image_id

        #XXX FIX remove hard code queue
        #route_key = self._current_fwdr['CONSUME_QUEUE']
        route_key = "f99_consume"
        self._publisher.publish_message(route_key, msg)



    def take_images_done(self, params):
        reply_queue = params['REPLY_QUEUE']
        readout_ack_id = params[ACK_ID]
        job_number = params[JOB_NUM]
        self.JOB_SCBD.set_value_for_job(job_number, 'STATE', 'TAKE_IMAGES_DONE')
        fwdr_readout_ack = self.get_next_timed_ack_id("AR_FWDR_TAKE_IMAGES_DONE_ACK")
        work_schedule = self.JOB_SCBD.get_work_schedule_for_job(job_number)
        fwdrs = work_schedule['FORWARDER_LIST']
        len_fwdrs = len(fwdrs)
        msg = {}
        msg[MSG_TYPE] = 'AR_FWDR_TAKE_IMAGES_DONE'
        msg[JOB_NUM] = job_number
        msg[ACK_ID] = fwdr_readout_ack
        for i in range (0, len_fwdrs):
            route_key = self.FWDR_SCBD.get_value_for_forwarder(fwdrs[i], 'CONSUME_QUEUE')
            self._publisher.publish_message(route_key, msg)

        ### FIX Add Final Response to DMCS

 
    def process_ack(self, params):
        """ Add new ACKS for a particular ACK_ID to the Ack Scoreboards
            where they are collated.

            :params: New ack to be checked in.

            :return: None.
        """
        pass
        #self.ACK_SCBD.add_timed_ack(params)
        

    def get_next_timed_ack_id(self, ack_type):
        """ Increment ack id by 1, and store it.
            Return ack id with ack type as a string.

            :params ack_type: Informational string to prepend Ack ID.

            :return retval: String with ack type followed by next ack id.
        """
        self._next_timed_ack_id = self._next_timed_ack_id + 1
        return (ack_type + "_" + str(self._next_timed_ack_id).zfill(6))


    def set_session(self, params):
        pass
        """ Record new session in JobScoreboard.
            Send AR_NEW_SESSION_ACK message with ack_bool equals True to specified reply queue.

            :params params: Dictionary with info about new session.

            :return: None.
        self.JOB_SCBD.set_session(params['SESSION_ID'])
        ack_id = params['ACK_ID']
        msg = {}
        msg['MSG_TYPE'] = 'AR_NEW_SESSION_ACK'
        msg['COMPONENT'] = self.COMPONENT_NAME
        msg['ACK_ID'] = ack_id
        msg['ACK_BOOL'] = True
        route_key = params['REPLY_QUEUE'] 
        self._publisher.publish_message(route_key, msg)
        """


    def get_current_session(self):
        """ Retreive current session from JobSocreboard.

            :params: None.

            :return: Current session returned by JobSocreboard.
        """
        return self.JOB_SCBD.get_current_session()


    def set_visit(self, params):
        """ Set current visit_id in JobScoreboard.
            Send AR_NEXT_VISIT_ACK message with ack_bool equals True to specified reply queue.

            :params params: Message dictionary with info about new visit.

            :return: None.
        """
        bore_sight = params['BORE_SIGHT']
        self.JOB_SCBD.set_visit_id(params['VISIT_ID'], bore_sight)
        ack_id = params['ACK_ID']
        msg = {}
        ## XXX FIXME Do something with the bore sight in params['BORE_SIGHT']
        msg['MSG_TYPE'] = 'AR_NEXT_VISIT_ACK'
        msg['COMPONENT'] = self.COMPONENT_NAME
        msg['ACK_ID'] = ack_id
        msg['ACK_BOOL'] = True
        route_key = params['REPLY_QUEUE'] 
        self._publisher.publish_message(route_key, msg)


    def get_current_visit(self):
        """ Retrieve current visit from JobSocreboard.

            :params: None.

            :return: Current visit returned by JobSocreboard.
        """
        return self.JOB_SCBD.get_current_visit()
        

    def ack_timer(self, seconds):
        """ Sleeps for user-defined seconds.

            :params seconds: Time to sleep in seconds.

            :return: True.
        """
        sleep(seconds)
        return True


    def progressive_ack_timer(self, ack_id, expected_replies, seconds):
        """ Sleeps for user-defined seconds, or less if everyone has reported back in.

            :params ack_id: Ack ID to wait for.

            :params expected_replies: Number of components expected to ack..

            :params seconds: Maximum time to wait in seconds.

            :return: The dictionary that represents the responses from the components ack'ing.
                     Note: If only one component will ack, this method breaks out of its
                           loop after the one ack shows up - effectively beating the maximum
                           wait time.
        """
        counter = 0.0
        while (counter < seconds):
            counter = counter + 0.5
            sleep(0.5)
            response = self.ACK_SCBD.get_components_for_timed_ack(ack_id)
            if response == None:
                continue
            if len(list(response.keys())) == expected_replies:
                return response

        ## Try one final time
        response = self.ACK_SCBD.get_components_for_timed_ack(ack_id)
        if response == None:
            return None
        elif len(list(response.keys())) == expected_replies:
            return response
        else:
            return None


    def extract_config_values(self):
        """ Parse system config yaml file.
            Throw error messages if Yaml file or key not found.

            :params: None.

            :return: True.
        """
        LOGGER.info('Reading YAML Config file %s' % self._config_file)
        try:
            cdm = toolsmod.intake_yaml_file(self._config_file)
        except IOError as e:
            LOGGER.critical("Unable to find CFG Yaml file %s\n" % self._config_file)
            sys.exit(101)

        try:
            self._msg_name = cdm[ROOT]['AUX_BROKER_NAME']      # Message broker user & passwd
            self._msg_passwd = cdm[ROOT]['AUX_BROKER_PASSWD']   
            self._msg_pub_name = cdm[ROOT]['AUX_BROKER_PUB_NAME']      # Message broker user & passwd
            self._msg_pub_passwd = cdm[ROOT]['AUX_BROKER_PUB_PASSWD']   
            self._base_broker_addr = cdm[ROOT][BASE_BROKER_ADDR]
            self._forwarder_dict = cdm[ROOT][XFER_COMPONENTS]['AUX_FORWARDERS']
            self._wfs_raft = cdm[ROOT]['ATS']['WFS_RAFT']

            # Placeholder until eventually worked out by Data Backbone team
            self.archive_fqn = cdm[ROOT]['ARCHIVE']['ARCHIVE_NAME']
            self.archive_name = cdm[ROOT]['ARCHIVE']['ARCHIVE_LOGIN']
            self.archive_ip = cdm[ROOT]['ARCHIVE']['ARCHIVE_IP']
            self.archive_xfer_root = cdm[ROOT]['ARCHIVE']['ARCHIVE_XFER_ROOT']
        except KeyError as e:
            print("Dictionary error")
            print("Bailing out...")
            sys.exit(99)

        self._base_msg_format = 'YAML'

        if 'BASE_MSG_FORMAT' in cdm[ROOT]:
            self._base_msg_format = cdm[ROOT]['BASE_MSG_FORMAT']


    def setup_consumer_threads(self):
        """ Create ThreadManager object with base broker url and kwargs to setup consumers.

            :params: None.

            :return: None.
        """
        base_broker_url = "amqp://" + self._msg_name + ":" + \
                                            self._msg_passwd + "@" + \
                                            str(self._base_broker_addr)
        LOGGER.info('Building _base_broker_url. Result is %s', base_broker_url)

        self.shutdown_event = threading.Event()
        self.shutdown_event.clear()


        # Set up kwargs that describe consumers to be started
        # The Archive Device needs three message consumers
        kws = {}
        md = {}
        md['amqp_url'] = base_broker_url
        md['name'] = 'Thread-aux_foreman_consume'
        md['queue'] = 'at_foreman_consume'
        md['callback'] = self.on_aux_foreman_message
        md['format'] = "YAML"
        md['test_val'] = None
        kws[md['name']] = md

        md = {}
        md['amqp_url'] = base_broker_url
        md['name'] = 'Thread-at_foreman_ack_publish'
        md['queue'] = 'at_foreman_ack_publish'
        md['callback'] = self.on_ack_message
        md['format'] = "YAML"
        md['test_val'] = 'test_it'
        kws[md['name']] = md

        md = {}
        md['amqp_url'] = base_broker_url
        md['name'] = 'Thread-archive_ctrl_publish'
        md['queue'] = 'archive_ctrl_publish'
        md['callback'] = self.on_archive_message
        md['format'] = "YAML"
        md['test_val'] = 'test_it'
        kws[md['name']] = md

        self.thread_manager = ThreadManager('thread-manager', kws, self.shutdown_event)
        self.thread_manager.start()



    def shutdown(self):
        LOGGER.info("Shutting down Consumer threads.")
        self.shutdown_event.set()
        LOGGER.debug("Thread Manager shutting down and app exiting...")
        print("\n")
        os._exit(0)


def main():
    logging.basicConfig(filename='logs/BaseForeman.log', level=logging.INFO, format=LOG_FORMAT)
    a_fm = AuxDevice()
    print("Beginning AuxForeman event loop...")
    try:
        while 1:
            pass
    except KeyboardInterrupt:
        a_fm.shutdown()
        pass

    print("")
    print("Aux Device Done.")



if __name__ == "__main__": main()
