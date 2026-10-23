/****************************************************************************
 * apps/examples/encfs_test/encfs_test_shared.h
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

#ifndef __APPS_EXAMPLES_ENCFS_TEST_SHARED_H
#define __APPS_EXAMPLES_ENCFS_TEST_SHARED_H

#include <stdbool.h>

void encfs_test_check(const char *label, bool condition);
void encfs_test_extended(void);
void encfs_test_corruption(void);
int encfs_test_stress(unsigned int seconds);

#endif
