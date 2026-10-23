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

static void usage(void)
{
  fprintf(stderr, "Usage: encfs start|stop|status|provision --erase\n");
#ifdef CONFIG_ENCFS_TESTING
  fprintf(stderr, "       encfs testkey <64-hex-digits>|board|none\n"
          "       Test keys only; stop the volume before changing keys.\n"
          "       board restores the board key; none simulates no key.\n");
#endif
}

#ifdef CONFIG_ENCFS_TESTING
static int hex_value(char ch)
{
  if (ch >= '0' && ch <= '9')
    {
      return ch - '0';
    }

  if (ch >= 'a' && ch <= 'f')
    {
      return ch - 'a' + 10;
    }

  if (ch >= 'A' && ch <= 'F')
    {
      return ch - 'A' + 10;
    }

  return -1;
}

static int test_key(FAR const char *text)
{
  uint8_t key[ENCFS_MASTER_KEY_SIZE] = {0};
  FAR volatile uint8_t *wipe = key;
  unsigned int i;
  int high;
  int low;
  int ret = -EINVAL;

  if (strcmp(text, "board") == 0)
    {
      ret = encfs_test_use_board_key();
    }
  else if (strcmp(text, "none") == 0)
    {
      ret = encfs_test_set_master(NULL, 0);
    }
  else if (strlen(text) == sizeof(key) * 2)
    {
      for (i = 0; i < sizeof(key); i++)
        {
          high = hex_value(text[i * 2]);
          low = hex_value(text[i * 2 + 1]);
          if (high < 0 || low < 0)
            {
              goto out;
            }

          key[i] = (uint8_t)((high << 4) | low);
        }

      ret = encfs_test_set_master(key, sizeof(key));
    }

out:
  /* Keep parsed key material out of the CLI's stack after every outcome. */

  for (i = 0; i < sizeof(key); i++)
    {
      wipe[i] = 0;
    }

  if (ret == -EINVAL)
    {
      fprintf(stderr, "encfs: testkey expects exactly 64 hexadecimal digits, "
              "board, or none\n");
    }
  else if (ret == -EBUSY)
    {
      fprintf(stderr, "encfs: stop the volume and release all resources "
              "before changing the test key\n");
    }
  else if (ret == 0)
    {
      printf("encfs: test key source changed\n");
    }

  return ret;
}
#endif

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
#ifdef CONFIG_ENCFS_TESTING
  else if (argc == 3 && strcmp(argv[1], "testkey") == 0)
    {
      ret = test_key(argv[2]);
    }
#endif
  else
    {
      usage();
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
