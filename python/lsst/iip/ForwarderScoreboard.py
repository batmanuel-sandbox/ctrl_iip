from Scoreboard import Scoreboard
import redis
import logging
from const import * 

LOG_FORMAT = ('%(levelname) -10s %(asctime)s %(name) -30s %(funcName) '
              '-35s %(lineno) -5d: %(message)s')
LOGGER = logging.getLogger(__name__)


class ForwarderScoreboard(Scoreboard):
    FORWARDER_ROWS = 'forwarder_rows'
    FWD_SCOREBOARD_DB = 6
    PUBLISH_QUEUE = 'forwarder_publish'
  

    def __init__(self, fdict):
        LOGGER.info('Setting up ForwarderScoreboard')
        self._redis = self.connect()
        self._redis.flushdb()

        forwarders = fdict.keys()
        for forwarder in forwarders:
            fields = fdict[forwarder]
            name = fields['NAME']
            routing_key = fields['CONSUME_QUEUE']
      
    
            for field in fields:
                self._redis.hset(forwarder, field, fields[field])
                self._redis.hset(forwarder, 'STATE', 'IDLE')
                self._redis.hset(forwarder, 'STATUS', 'HEALTHY')
                self._redis.hset(forwarder, 'ROUTING_KEY', routing_key)
      
            self._redis.lpush(self.FORWARDER_ROWS, forwarder)
    
      #self.persist_snapshot(self._redis)


    def connect(self):
        pool = redis.ConnectionPool(host='localhost', port=6379, db=self.FWD_SCOREBOARD_DB)
        return redis.Redis(connection_pool=pool)


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


    def set_forwarder_params(self, forwarder, params):
        for kee in params.keys():
            self._redis.hset(forwarder, kee, params[kee])

        #self.persist_snapshot(self._redis)
    

    def set_value_for_multiple_forwarders(self, forwarders, kee, val):
        for forwarder in forwarders:
            self._redis.hset(forwarder, kee, val)


    def set_params_for_multiple_forwarders(self, forwarders, params):
        for forwarder in forwarders:
            kees = params.keys()
            for kee in kees:
                self._redis.hset(forwarder, kee, params[kee])


    def get_value_for_forwarder(self, forwarder, kee):
        return self._redis.hget(forwarder, kee)


    def set_forwarder_state(self, forwarder, state):
        self._redis.hset(forwarder, 'STATE', state)


    def set_forwarder_status(self, forwarder, status):
        self._redis.hset(forwarder, 'STATUS', status)


    def get_routing_key(self, forwarder):
        return self._redis.hget(forwarder,'ROUTING_KEY')


    def print_all(self):
        all_forwarders = self.return_forwarders_list()
        for forwarder in all_forwarders:
            print forwarder
            print self._redis.hgetall(forwarder)
        print "--------Finished In print_all--------"


    # def add_forwarder_row(self, fdict): #NAME, HOSTNAME, IP_ADDR, STATUS, STATE
    #   forwarders = self._redis.lrange(self.Forwarder_rows, 0, -1)
    #   for forwarder in forwarders:
    #     self._redis.lpush(self.Forwarder_rows, forwarder)
    #     fields = forwarder.keys()
    #     for field in fields: 
    #       Redis_fwd_conn.hset(forwarder, field, fields[field])
    #  ## Add name to queue names list 

