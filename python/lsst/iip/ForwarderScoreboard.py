from Scoreboard import Scoreboard
import redis
import sys
import yaml
import logging
from const import * 

LOG_FORMAT = ('%(levelname) -10s %(asctime)s %(name) -30s %(funcName) '
              '-35s %(lineno) -5d: %(message)s')
LOGGER = logging.getLogger(__name__)


class ForwarderScoreboard(Scoreboard):
    FORWARDER_ROWS = 'forwarder_rows'
    PUBLISH_QUEUE = 'forwarder_publish'
    DB_TYPE = ""
    DB_INSTANCE = None
  

    def __init__(self, db_type, db_instance, fdict):
        LOGGER.info('Setting up ForwarderScoreboard')
        self.DB_TYPE = db_type
        self.DB_INSTANCE = db_instance
        self._redis = self.connect()
        self._redis.flushdb()

        forwarders = list(fdict.keys()) 
        for forwarder in forwarders:
            fields = fdict[forwarder]
            name = fields['NAME']
            routing_key = fields['CONSUME_QUEUE']
      
    
            for field in fields:
                self._redis.hset(forwarder, field, fields[field])
                self._redis.hset(forwarder, 'STATE', 'IDLE')
                self._redis.hset(forwarder, 'STATUS', 'HEALTHY')
                self._redis.hset(forwarder, 'CONSUME_QUEUE', routing_key)
      
            self._redis.lpush(self.FORWARDER_ROWS, forwarder)
    
      #self.persist_snapshot(self._redis)


    def connect(self):
        #pool = redis.ConnectionPool(host='localhost', port=6379, db=self.DB_INSTANCE)
        #return redis.Redis(connection_pool=pool)
        try:
            sconn = redis.StrictRedis(host='localhost',port='6379', \
                                      charset='utf-8', db=self.DB_INSTANCE, \
                                      decode_responses=True)
            sconn.ping()
            LOGGER.info("Redis connected. Connection details are: %s", sconn)
            return sconn
        except Exception as e:
            LOGGER.critical("Redis connection error: %s", e)
            LOGGER.critical("Exiting due to Redis connection failure.")
            sys.exit(100)


    def return_forwarders_list(self):
        all_forwarders = self._redis.lrange(self.FORWARDER_ROWS, 0, -1)
        return all_forwarders


    def return_healthy_forwarders_list(self):
        healthy_forwarders = []
        forwarders = self._redis.lrange(self.FORWARDER_ROWS, 0, -1)
        for forwarder in forwarders:
            if self._redis.hget(forwarder, 'STATUS') == 'HEALTHY':
                healthy_forwarders.append(forwarder)

        return healthy_forwarders


    def return_available_forwarders_list(self):
        available_forwarders = []
        forwarders = self._redis.lrange(self.FORWARDER_ROWS, 0, -1)
        for forwarder in forwarders:
            if ((self._redis.hget(forwarder, 'STATUS')) == 'HEALTHY') and ((self._redis.hget(forwarder, 'STATE'))  == 'IDLE'):
                available_forwarders.append(forwarder)

        return available_forwarders


    def setall_forwarders_status(self, status):
        forwarders = self._redis.lrange(self.FORWARDER_ROWS, 0, -1)
        for forwarder in forwarders:
            self._redis.hset(forwarder, 'STATUS', status)
        #self.persist_snapshot(self._redis, "forwarderscoreboard")


    def set_forwarder_params(self, forwarders, params):
        """This is the workhorse of scoreboard getter/setter methods.
           :param list forwarders: One or many forwarders can be set, depending on size of list in this arg.           
           :param dict params: One or many fields can be set depending on number of entries in this arg.
        """
        for forwarder in forwarders:
            kees = list(params.keys())
            for kee in kees:
                self._redis.hset(forwarder, kee, params[kee])
        #self.persist_snapshot(self._redis, "forwarderscoreboard")


    def setall_forwarder_params(self, params):
        forwarders = self._redis.lrange(self.FORWARDER_ROWS, 0, -1)
        self.set_forwarder_params(forwarders, params)

    def get_value_for_forwarder(self, forwarder, kee):
        return self._redis.hget(forwarder, kee)


    def set_forwarder_state(self, forwarder, state):
        self._redis.hset(forwarder, 'STATE', state)
        #self.persist_snapshot(self._redis, "forwarderscoreboard")


    def set_forwarder_status(self, forwarder, status):
        self._redis.hset(forwarder, 'STATUS', status)
        #self.persist_snapshot(self._redis, "forwarderscoreboard")


    def get_routing_key(self, forwarder):
        return self._redis.hget(forwarder,'CONSUME_QUEUE')


    def set_work_by_job(self, forwarder, job_num, work_schedule):
        self._redis.hset(forwarder, job_num, yaml.dump(work_schedule))


    def get_work_by_job(self, forwarder, job_num):
        work_schedule = self._redis.hget(forwarder, job_num)
        return yaml.load(work_schedule)


    def print_all(self):
        all_forwarders = self.return_forwarders_list()
        for forwarder in all_forwarders:
            print(forwarder)
            print(self._redis.hgetall(forwarder))
        print("--------Finished In print_all--------")


    # def add_forwarder_row(self, fdict): #NAME, HOSTNAME, IP_ADDR, STATUS, STATE
    #   forwarders = self._redis.lrange(self.Forwarder_rows, 0, -1)
    #   for forwarder in forwarders:
    #     self._redis.lpush(self.Forwarder_rows, forwarder)
    #     fields = forwarder.keys()
    #     for field in fields: 
    #       Redis_fwd_conn.hset(forwarder, field, fields[field])
    #  ## Add name to queue names list 


