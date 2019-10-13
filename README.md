![banner](docs/banner.png)
# THOR
Tracklet-less Heliocentric Orbit Recovery  
[![Build Status](https://dev.azure.com/moeyensj/thor/_apis/build/status/moeyensj.thor?branchName=master)](https://dev.azure.com/moeyensj/thor/_build/latest?definitionId=2&branchName=master)
[![Build Status](https://www.travis-ci.com/moeyensj/thor.svg?token=sWjpnqPgpHyuq3j7qPuj&branch=master)](https://www.travis-ci.com/moeyensj/thor)
[![Coverage Status](https://coveralls.io/repos/github/moeyensj/thor/badge.svg?branch=master&t=pdSkQA)](https://coveralls.io/github/moeyensj/thor?branch=master)
[![Docker Pulls](https://img.shields.io/docker/pulls/moeyensj/thor)]
[![License](https://img.shields.io/badge/License-BSD%203--Clause-blue.svg)](https://opensource.org/licenses/BSD-3-Clause)

## Installation
To install pre-requisite software using anaconda: 

```conda install -c defaults -c conda-forge --file requirements.txt```

To install pre-requisite software using pip:

```pip install -r requirements.txt```

Once pre-requisites have been installed:

```python setup.py install```

## Testing Installation

Using pytest (with coveralls):

```pytest thor --cov=thor```

Or using setuptools:

```python setup.py test```
