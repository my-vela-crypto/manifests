# SPDX-License-Identifier: Apache-2.0
if(CONFIG_EXAMPLES_ENCFS_TEST)
  set(SRCS encfs_test_main.c encfs_test_extended.c encfs_test_corruption.c
           encfs_test_stress.c encfs_test_utf8.c)
  if(CONFIG_ENCFS_KEYWRAP AND CONFIG_ENCFS_TESTING)
    list(APPEND SRCS encfs_test_rekey.c)
  endif()
  nuttx_add_application(NAME encfs_test
    SRCS ${SRCS}
    STACKSIZE 16384 PRIORITY 100)
endif()
