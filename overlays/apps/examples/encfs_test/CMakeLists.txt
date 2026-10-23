# SPDX-License-Identifier: Apache-2.0
if(CONFIG_EXAMPLES_ENCFS_TEST)
  nuttx_add_application(NAME encfs_test
    SRCS encfs_test_main.c encfs_test_extended.c encfs_test_corruption.c
    STACKSIZE 16384 PRIORITY 100)
endif()
