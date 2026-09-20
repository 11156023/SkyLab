import uuid
from types import SimpleNamespace

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import IpAllocation
from app.services.network import ip_management_service


def test_release_ip_with_reservation_key_only_releases_its_own_row() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add_all(
                [
                    IpAllocation(
                        ip_address="10.0.0.10",
                        purpose="lxc",
                        vmid=487,
                        reservation_key="class:node-a:user",
                    ),
                    IpAllocation(
                        ip_address="10.0.0.11",
                        purpose="lxc",
                        vmid=487,
                        reservation_key="class:node-b:user",
                    ),
                ]
            )
            session.commit()

            released = ip_management_service.release_ip(
                session,
                487,
                restore_reservation=True,
                reservation_key="class:node-b:user",
            )
            session.commit()

            assert released == "10.0.0.11"
            rows = list(session.exec(select(IpAllocation).order_by(IpAllocation.ip_address)))
            assert rows[0].vmid == 487
            assert rows[1].vmid is None
            assert rows[1].purpose == "class_reserved"
    finally:
        engine.dispose()


def test_stale_class_reservation_is_reclaimed_only_for_another_node() -> None:
    class_id = uuid.uuid4()
    user_id = uuid.uuid4()
    row = IpAllocation(
        ip_address="10.0.0.12",
        purpose="lxc",
        vmid=487,
        reservation_key=f"{class_id}:node-b:{user_id}",
        teaching_class_id=class_id,
    )
    resource = SimpleNamespace(
        vmid=487,
        teaching_class_id=class_id,
        user_id=user_id,
        batch_job_id=uuid.uuid4(),
    )
    job = SimpleNamespace(id=resource.batch_job_id, teaching_class_id=class_id)
    class FakeResult:
        def first(self):
            return None

    class FakeSession:
        def get(self, model, key):
            from app.models import BatchProvisionJob, Resource

            if model is Resource and key == 487:
                return resource
            if model is BatchProvisionJob and key == resource.batch_job_id:
                return job
            return None

        def exec(self, _statement):
            # The real statement asks for node-b; no row models a different
            # logical node for this resource.
            return FakeResult()

        def add(self, _value):
            pass

        def flush(self):
            pass

    assert ip_management_service._reclaim_stale_class_reservation(
        FakeSession(), row
    )
    assert row.vmid is None
    assert row.resource_vmid is None
    assert row.purpose == "class_reserved"
