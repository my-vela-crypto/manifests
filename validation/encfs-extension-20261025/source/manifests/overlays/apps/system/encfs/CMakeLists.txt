# SPDX-License-Identifier: Apache-2.0
if(CONFIG_SYSTEM_ENCFS)
  nuttx_add_application(NAME encfs SRCS encfs_main.c STACKSIZE 16384 PRIORITY 100)
endif()
