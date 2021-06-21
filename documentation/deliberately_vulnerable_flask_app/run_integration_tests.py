# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import json
import os.path
import subprocess


class PysaIssuesMismatchException(Exception):
    """
    Custom exception to raise when there is a mismatch with the issues
    pysa generates and those in true_issues.json
    """

    pass


def parse_issues(output: str) -> list:
    """
    Parses issues returned in output. Returns a list of dicts containing
    minimal information about the respective issue.
    """
    parsed_output: list = json.loads(output)
    minimal_list: list = list(
        map(
            lambda issue: {
                "path": issue["path"],
                "define": issue["define"],
                "code": issue["code"],
            },
            parsed_output,
        )
    )
    return minimal_list


def compare_issues(expected_issues: list, actual_issues: list) -> None:
    """
    Compare both the results. Raises an exception and saves actual issues to results.actual
    if there is a mismatch. Prints test successful if all actual issues are expected issues.
    """
    # Go over each issue in actual_issues and see if they are in expected_issues
    additional_issues: list = []
    for issue in actual_issues:
        if issue in expected_issues:
            expected_issues[expected_issues.index(issue)]["detected"] = True
        else:
            additional_issues.append(issue)

    additional_issues_count: int = len(additional_issues)
    if additional_issues_count != 0:
        print(
            "pysa detected the following {} additional issue(s)".format(
                additional_issues_count
            )
        )
        print(json.dumps(additional_issues))

    undetected_issues: list = list(
        filter(
            lambda issue: True if "detected" not in issue.keys() else False,
            expected_issues,
        )
    )
    undetected_issues_count: int = len(undetected_issues)
    if undetected_issues_count != 0:
        print(
            "pysa failed to detect the following {} issues(s)".format(
                undetected_issues_count
            )
        )
        print(json.dumps(undetected_issues))

    if undetected_issues_count == 0 and additional_issues_count == 0:
        print("Test passed successfully")
    else:
        with open("results.actual", "w") as f:
            f.write(json.dumps(actual_issues))
        print("Test failed, results written to results.actual")
        raise PysaIssuesMismatchException(
            "The generated pysa issues don't match with those specified in results.expected"
        )


def run_test() -> None:
    """
    Entry point function which checks if resulsts.expected is there, parses it,
    runs pysa, and call functions to parse and compare the output.
    """
    if not os.path.isfile("./results.expected"):
        raise FileNotFoundError(
            "./results.expected file that specifies issues to expect is not found"
        )

    # Parse the predefined true_issues.json file data
    expected_issues: list = json.load(open("results.expected", "r"))

    print("Expecting {} issues".format(len(expected_issues)))

    # Run Pysa and parse the output
    actual_issues: list = parse_issues(
        subprocess.check_output("pyre analyze --no-verify", text=True, shell=True)
    )

    print("Pysa detected {} issues".format(len(actual_issues)))

    compare_issues(expected_issues, actual_issues)


if __name__ == "__main__":
    run_test()
