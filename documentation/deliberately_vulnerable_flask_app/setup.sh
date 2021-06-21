#!/bin/bash
# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# Source this file to run it:
# . ./setup.sh

pip install -r requirements.txt
rm ../../.pyre_configuration
# Create .pyre_configuration with as ../../stubs as taint_models_path
echo '{"source_directories": ["."], "taint_models_path": "../../stubs"}' > ./.pyre_configuration
