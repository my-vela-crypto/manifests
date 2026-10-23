# SPDX-License-Identifier: Apache-2.0
include $(APPDIR)/Make.defs
PROGNAME = encfs_test
PRIORITY = 100
STACKSIZE = 16384
MODULE = $(CONFIG_EXAMPLES_ENCFS_TEST)
MAINSRC = encfs_test_main.c
CSRCS = encfs_test_extended.c encfs_test_corruption.c encfs_test_stress.c
include $(APPDIR)/Application.mk
