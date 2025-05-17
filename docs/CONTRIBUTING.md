# CONTRIBUTING

Dubbo Python is released under the non-restrictive Apache 2.0 license and follows a standard GitHub development process. We use GitHub Issues for tracking bugs and feature requests, and we merge Pull Requests (PRs) into the `main` branch. Contributions of all kinds are welcome, provided they adhere to the community guidelines below.



### Sign the Contributor License Agreement (CLA)

Before we accept a non-trivial patch or pull request (PRs), we will need you to sign the Contributor License Agreement. Signing the contributors' agreement does not grant anyone commits rights to the main repository, but it does mean that we can accept your contributions, and you will get an author credit if we do. Active contributors may get invited to join the core team that will grant them privileges to merge existing PRs.



### Communication Channels

#### Mailing list

The mailing list is the recommended way of pursuing a discussion on almost anything related to Dubbo. Please refer to this [guide](https://github.com/apache/dubbo/wiki/Mailing-list-subscription-guide) for detailed documentation on how to subscribe.

- [dev@dubbo.apache.org](mailto:dev-subscribe@dubbo.apache.org): the developer mailing list where you can ask questions about an issue you may have encountered while working with Dubbo.
- [commits@dubbo.apache.org](mailto:commits-subscribe@dubbo.apache.org): the commit updates will get broadcasted on this mailing list. You can subscribe to it, should you be interested in following Dubbo's development.
- [notifications@dubbo.apache.org](mailto:notifications-subscribe@dubbo.apache.org): all the Github [issue](https://github.com/apache/dubbo/issues) updates and [pull request](https://github.com/apache/dubbo/pulls) updates will be sent to this mailing list.

#### Instant Messaging

We maintain a DingTalk group for real-time chat:

> **Group ID:** 105120013829



### Reporting issue

To report a bug or request a feature, use the [GitHub Issue templates](https://github.com/apache/dubbo/issues/new/choose).
**Note:** Issues specific to the Python SDK should be filed in the main [Dubbo repository](https://github.com/apache/dubbo/issues) under the **Apache Dubbo Component** option, selecting `Python SDK`.



### Code Conventions

Our Python code follows the [PEP 8 style guide](https://peps.python.org/pep-0008/) with these adjustments:

1. **Maximum Line Length:** Up to 120 characters.
2. **Docstrings and Comments:** Use reStructuredText format.
3. **Type Hints:** All public functions and methods must include type annotations consistent with [PEP 484](https://peps.python.org/pep-0484/).
4. **Imports:** Grouped in the order: standard library, third-party, local imports, each separated by a blank line.
5. **Indentation:** 4 spaces; no tabs.

Please run linters before submitting code (see "Formatting and Linting" below).



### Contribution Workflow

1. **Fork** the repository: `https://github.com/apache/dubbo-python.git`

2. **Sync** your fork with upstream:

   ```sh
   git remote add upstream git@github.com:apache/dubbo-python.git
   git fetch upstream
   git checkout main
   git rebase upstream/main
   ```

3. **Create** a topic branch from `main`:

   ```sh
   git checkout -b feature/my-awesome-feature
   ```

4. **Develop** in logical commits; follow the proper commit message format:

   ```sh
   <type>(<scope>): <subject>

   <BLANK LINE>
   <body>
   ```

   - **type:** feat, fix, docs, style, perf, test, chore
   - **scope:** optional component or module
   - **subject:** short description

5. **Push** your branch

   ```sh
   git push origin feature/my-awesome-feature
   ```

6. **Open a Pull Request** against `apache/dubbo-python:main`.

7. **Complete** the PR checklist in our [Pull Request Template](https://github.com/apache/dubbo-python/blob/main/.github/PULL_REQUEST_TEMPLATE.md).

8. **Address** review comments until the PR is approved and merged.

Thank you for contributing!



### Development Environment Setup

We recommend using [uv](https://github.com/astral-sh/uv) to manage dependencies and scripts.

1. **Install uv** per the [official guide](https://docs.astral.sh/uv/getting-started/installation/).

2. **Synchronize** dependencies:

   ```sh
   # The dev group is synced by default
   uv sync
   ```



### Formatting and Linting

We use [ruff](https://github.com/astral-sh/ruff) for linting and formatting, and [mypy](https://github.com/python/mypy) for static type checking.

Before committing, ensure code style and types are clean:

```sh
# From the project root, where pyproject.toml resides
# Run ruff to lint and auto-fix issues
uv run ruff check --fix

# Run mypy for type checking
uv run mypy src/dubbo
```

#### Pre-commit Hooks

We use [pre-commit](https://pre-commit.com) for automatic formatting on commit.

```sh
# Install Git hooks
uv run pre-commit install

# Optionally, run against all files
pre-commit run --all-files
```



### Testing

Run the test suite and generate a coverage report:

```sh
# Run pytest with coverage
uv run pytest --cov-report=html

# Open the HTML report
cd htmlcov
# macOS
open index.html
# Linux
xdg-open index.html
# Windows (PowerShell)
start index.html
```



### Build and Release (Optional)

We follow the official [Python packaging guide](https://packaging.python.org/en/latest/) and use [hatch](https://hatch.pypa.io/latest/publish/) for publishing releases.



#### Build Distributions

1. **Sync build dependencies:**

   ```sh
   # Contains build and dev groups
   uv sync --group build

   # Or only build group
   uv sync --only-group build
   ```

2. **Build distributions:**

   ```sh
   # Generate source and wheel distributions
   uv run hatch build

   # To build a specific format, e.g., wheel only:
   uv run hatch build -t wheel
   ```




#### Manual Publishing to PyPI (Not Recommended)

Our GitHub Actions workflow automates publishing to PyPI; manual release is only used for special cases.

```sh
uv run hatch publish
```

For more details, refer to https://hatch.pypa.io/latest/publish/



#### Automated CI/CD Release

> Reference: [Publishing package distribution releases using GitHub Actions CI/CD workflows](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)

##### Prerequisites: Configuring Trusted Publishing

Follow the **[Configuring Trusted Publishing](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/#configuring-trusted-publishing)** section of the official guide to ensure that only manually approved workflow runs are permitted to publish to PyPI.

**NOTE**: For security reasons, you must require [manual approval](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment#deployment-protection-rules) on each run for the `pypi` environment.

##### Trigger Conditions

The `publish` workflow is triggered whenever a new Release is created in the GitHub repository. It will build the source code corresponding to that Release, upload the resulting artifacts, and then publish the package to PyPI.
