

/*
 * This file contains the implementation for the atArchiver_stop commander test.
 *
 ***/

#include <string>
#include <sstream>
#include <iostream>
#include "SAL_atArchiver.h"
#include "ccpp_sal_atArchiver.h"
#include "os.h"
#include <stdlib.h>
using namespace DDS;
using namespace atArchiver;

int main (int argc, char *argv[])
{ 
  int cmdId;
  int timeout=10;
  int status=0;

  atArchiver_command_stopC myData;
  if (argc < 2 ) {
     printf("Usage :  input parameters...\n");
     printf("  boolean	state;\n");
     exit(1);
  }
  SAL_atArchiver mgr = SAL_atArchiver();

  mgr.salCommand("atArchiver_command_stop");

  myData.device   = "all";
  myData.property = "motion";
  myData.action   = "stop";
    sscanf(argv[1], "%d", &myData.state);

  // generate command
  cmdId = mgr.issueCommand_stop(&myData);
  cout << "=== command stop issued = " << endl;
  status = mgr.waitForCompletion_stop(cmdId, timeout);

  /* Remove the DataWriters etc */
  mgr.salShutdown();
  if (status != SAL__CMD_COMPLETE) {
     exit(1);
  }
  exit(0);
}


