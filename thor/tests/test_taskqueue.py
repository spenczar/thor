"""
This file contains integration tests, which may be slow and require setup.
"""

import os
import pytest
import pandas as pd
import pandas.testing as pd_testing
import pika
import uuid
import logging
import tempfile

from google.cloud.storage import Client as GCSClient
import google.cloud.exceptions

from thor import config
from thor.orbits import Orbits
from thor.taskqueue import tasks
from thor.taskqueue import queue
from thor.taskqueue import jobs
from thor.taskqueue import client
from thor.testing import integration_test

RUN_INTEGRATION_TESTS = "THOR_INTEGRATION_TEST" in os.environ

_RABBIT_HOST = os.environ.get("RABBIT_HOST", "localhost")
_RABBIT_PORT = os.environ.get("RABBIT_PORT", 5672)
_RABBIT_USER = os.environ.get("RABBIT_USER", "thor")
_RABBIT_PASSWORD = os.environ.get("RABBIT_PASSWORD", None)


DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "testing", "data",
)

logger = logging.getLogger("thor")


@pytest.fixture()
def queue_connection(request):
    if _RABBIT_PASSWORD is None:
        raise ValueError(
            "you must set RABBIT_PASSWORD env variable for integration tests",
        )
    queue_name = request.function.__name__
    conn_params = pika.ConnectionParameters(
        host=_RABBIT_HOST,
        port=_RABBIT_PORT,
        credentials=pika.PlainCredentials(
            username=_RABBIT_USER, password=_RABBIT_PASSWORD
        ),
    )
    conn = queue.TaskQueueConnection(conn_params, queue_name)
    conn.connect()
    yield conn
    conn.channel.queue_delete(queue_name)
    conn.close()


@pytest.fixture()
def google_storage_bucket(request):
    client = GCSClient()
    bucket_name = f"test_bucket__{request.function.__name__}"
    try:
        bucket = client.create_bucket(bucket_name)
    except google.cloud.exceptions.Conflict:
        logger.warning(
            "bucket %s already exists; tests may be unpredictable", bucket_name
        )
        bucket = client.bucket(bucket_name)
    yield bucket
    bucket.delete(force=True, client=client)


@pytest.fixture()
def observations():
    yield pd.read_csv(
        os.path.join(DATA_DIR, "observations.csv"),
        index_col=False,
        dtype={"obs_id": str},
    )


@pytest.fixture()
def orbits():
    yield Orbits.from_csv(os.path.join(DATA_DIR, "orbits.csv"))


test_config = config.Configuration(cluster_link_config={"vx_bins": 10, "vy_bins": 10})


@integration_test
def test_queue_roundtrip(queue_connection, google_storage_bucket, observations, orbits):
    job_id = str(uuid.uuid1())
    tasks.upload_job_inputs(google_storage_bucket, job_id, test_config, observations)
    task = tasks.Task.create(
        job_id=job_id, bucket=google_storage_bucket, orbits=orbits,
    )
    queue_connection.publish(task)
    have = queue_connection.receive()
    assert have.job_id == task.job_id
    assert have.bucket == task.bucket
    assert have.task_id == task.task_id

    have_config, have_obs, have_orb = tasks.download_task_inputs(
        google_storage_bucket, have,
    )

    assert have_config == test_config
    pd_testing.assert_frame_equal(have_obs, observations)
    assert have_orb == orbits

    # have.mark_success()


def test_new_task_id_single_orbit(orbits):
    single_orbit = orbits.split(1)[0]
    have = tasks.new_task_id(single_orbit)
    assert have == single_orbit.ids[0]


def test_new_task_id_multiple_orbits(orbits):
    have = tasks.new_task_id(orbits)
    have_uuid = uuid.UUID(have)
    # We don't actually care very much that its UUID version 3, but this is a
    # convenient assertion to be sure it was parsed properly.
    assert have_uuid.version == 3


def test_job_manifest_serialization_roundtrip(orbits):
    manifest = jobs.JobManifest.create("job_id")
    for i, orbit in enumerate(orbits.split(1)[:5]):
        task = tasks.Task(manifest.job_id, f"task-{i}", "test-bucket", None, -1)
        manifest.append(orbit, task)

    as_str = manifest.to_str()
    have = jobs.JobManifest.from_str(as_str)

    assert have.job_id == manifest.job_id
    assert have.creation_time == manifest.creation_time
    assert have.orbit_ids == manifest.orbit_ids
    assert have.task_ids == manifest.task_ids


@integration_test
def test_job_manifest_storage_roundtrip(google_storage_bucket, orbits):
    manifest = jobs.JobManifest.create("test_job_id")
    for i, orbit in enumerate(orbits.split(1)[:5]):
        task = tasks.Task(
            manifest.job_id, f"task-{i}", google_storage_bucket.name, None, -1
        )
        manifest.append(orbit, task)

    jobs.upload_job_manifest(google_storage_bucket, manifest)

    have = jobs.download_job_manifest(google_storage_bucket, manifest.job_id)

    assert have.job_id == manifest.job_id
    assert have.creation_time == manifest.creation_time
    assert have.orbit_ids == manifest.orbit_ids
    assert have.task_ids == manifest.task_ids


@integration_test
def test_client_roundtrip(
    queue_connection, google_storage_bucket, orbits, observations
):
    taskqueue_client = client.Client(google_storage_bucket, queue_connection)
    taskqueue_worker = client.Worker(GCSClient(), queue_connection)

    # trim down to 3 orbits
    orbits = Orbits.from_df(orbits.to_df()[:3])
    n_task = 3

    manifest = taskqueue_client.launch_job(test_config, observations, orbits)
    assert len(manifest.task_ids) == n_task

    statuses = taskqueue_client.get_task_statuses(manifest)
    assert len(statuses) == n_task

    assert all(
        s.state == tasks.TaskState.REQUESTED for s in statuses.values()
    ), "all tasks should initially be in 'requested' state"

    received_tasks = list(taskqueue_worker.poll_for_tasks(poll_interval=0.5, limit=5))
    assert len(received_tasks) == n_task

    statuses = taskqueue_client.get_task_statuses(manifest)
    assert all(
        s.state == tasks.TaskState.IN_PROGRESS for s in statuses.values()
    ), "all tasks should be in 'in_progress' state once received"

    # Handle the first task. It should be marked as succeeded, but others still
    # in progress.
    taskqueue_worker.handle_task(received_tasks[0])
    statuses = taskqueue_client.get_task_statuses(manifest)
    task1_state, task2_state, task3_state = (
        statuses[received_tasks[0].task_id].state,
        statuses[received_tasks[1].task_id].state,
        statuses[received_tasks[2].task_id].state,
    )
    assert task1_state == tasks.TaskState.SUCCEEDED
    assert task2_state == tasks.TaskState.IN_PROGRESS
    assert task3_state == tasks.TaskState.IN_PROGRESS

    # Download results. We should only have results for the first task.
    with tempfile.TemporaryDirectory(prefix="thor.test_client_roundtrip_1") as outdir:
        taskqueue_client.download_results(manifest, outdir)
        _assert_results_downloaded(outdir, received_tasks[0].task_id)

    # Handle another task.
    taskqueue_worker.handle_task(received_tasks[1])
    statuses = tasks.get_task_statuses(google_storage_bucket, manifest)
    task1_state, task2_state, task3_state = (
        statuses[received_tasks[0].task_id].state,
        statuses[received_tasks[1].task_id].state,
        statuses[received_tasks[2].task_id].state,
    )
    assert task1_state == tasks.TaskState.SUCCEEDED
    assert task2_state == tasks.TaskState.SUCCEEDED
    assert task3_state == tasks.TaskState.IN_PROGRESS

    # Download results. Now we should have results for the first two tasks.
    with tempfile.TemporaryDirectory(prefix="thor.test_client_roundtrip_2") as outdir:
        taskqueue_client.download_results(manifest, outdir)
        _assert_results_downloaded(outdir, received_tasks[0].task_id)
        _assert_results_downloaded(outdir, received_tasks[1].task_id)


def _assert_results_downloaded(dir: str, task_id: str):
    # There should be a folder for the entire task
    task_folder_path = os.path.join(dir, "tasks", f"task-{task_id}", "outputs")
    assert os.path.exists(task_folder_path)

    # There should be a log file in that folder
    assert os.path.exists(os.path.join(task_folder_path, "thor.log"))

    # There should be a file of recovered orbits, and of recovered orbit
    # members.
    assert os.path.exists(os.path.join(task_folder_path, "recovered_orbits.csv"))
    assert os.path.exists(os.path.join(task_folder_path, "recovered_orbit_members.csv"))
