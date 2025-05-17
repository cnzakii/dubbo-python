#!/bin/sh

#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

export SOURCE_FILES="src/dubbo"
export TEST_FILES="tests"

# Function to check if a command exists and return the appropriate command string
get_command() {
    local cmd_name=$1
    local cmd_display_name=$2

    # First try direct command
    if command -v "$cmd_name" >/dev/null 2>&1; then
        echo "INFO: Using direct command for $cmd_display_name" >&2
        echo "$cmd_name"
        return 0
    fi

    # Then try with uv
    if command -v uv >/dev/null 2>&1; then
        echo "INFO: Using uv to run $cmd_display_name" >&2
        echo "uv run $cmd_name"
        return 0
    fi

    # If we get here, neither option worked
    echo "Error: $cmd_display_name is not installed. Please install it to run linting." >&2
    return 1
}

# Linting
echo "Running linting..."

# Get ruff command
RUFF_CMD=$(get_command ruff Ruff) || exit 1

# Run ruff commands
echo "Running ruff format..."
$RUFF_CMD format $SOURCE_FILES $TEST_FILES --diff

echo "Running ruff check..."
$RUFF_CMD check --output-format=github $SOURCE_FILES $TEST_FILES --config=pyproject.toml

# Get mypy command
MYPY_CMD=$(get_command mypy Mypy) || exit 1

# Run mypy command
echo "Running mypy..."
$MYPY_CMD $SOURCE_FILES --config-file=pyproject.toml

echo "Linting completed."
