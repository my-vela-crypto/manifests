/****************************************************************************
 * apps/system/encfs/encfs_main.c
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

#include <nuttx/config.h>
#include <nuttx/fs/encfs.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *state_name(enum encfs_state_e state)
{
  static const char *const names[] =
  {
    "STOPPED", "BLOCK_REGISTERED", "BLOCK_KEYED", "LOWER_MOUNTED", "READY"
  };

  return state >= ENCFS_STOPPED && state <= ENCFS_READY ?
         names[state] : "INVALID";
}

int main(int argc, FAR char *argv[])
{
  struct encfs_status_s status;
  int ret;

  if (argc == 2 && strcmp(argv[1], "start") == 0)
    {
      ret = encfs_start();
    }
  else if (argc == 2 && strcmp(argv[1], "stop") == 0)
    {
      ret = encfs_stop();
    }
  else if (argc == 3 && strcmp(argv[1], "provision") == 0 &&
           strcmp(argv[2], "--erase") == 0)
    {
      /* Normal startup never reaches this destructive operation. */

      printf("encfs: formatting encrypted data volume\n");
      ret = encfs_provision();
    }
  else if (argc == 2 && strcmp(argv[1], "status") == 0)
    {
      ret = 0;
    }
  else
    {
      fprintf(stderr, "Usage: encfs start|stop|status|provision --erase\n");
      return EXIT_FAILURE;
    }

  if (encfs_get_status(&status) == 0)
    {
      printf("encfs: state=%s result=%d stage=%d error=%d "
             "cleanup=%d/%d block=%d keyed=%d lower=%d upper=%d\n",
             state_name(status.state), ret, status.stage, status.error,
             status.cleanup_stage, status.cleanup_error,
             status.block_registered, status.block_keyed,
             status.lower_visible, status.upper_mounted);
    }

  if (ret < 0)
    {
      fprintf(stderr, "encfs: %s (%d)\n", strerror(-ret), ret);
    }

  return ret < 0 ? EXIT_FAILURE : EXIT_SUCCESS;
}
