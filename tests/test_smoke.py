"""
Smoke tests for apxctrl module.

These tests verify that all modules can be imported without syntax errors.
Run with: pytest test_smoke.py -v
"""
import pytest


class TestImports:
    """Test that all modules can be imported."""

    def test_import_models(self):
        """Test that models.py can be imported."""
        import apxctrl.model
        
        # Verify key classes exist
        assert hasattr(apxctrl.model, "APxState")
        assert hasattr(apxctrl.model, "ServerState")
        assert hasattr(apxctrl.model, "ProjectInfo")
        assert hasattr(apxctrl.model, "SequenceInfo")
        assert hasattr(apxctrl.model, "SignalPathInfo")
        assert hasattr(apxctrl.model, "MeasurementInfo")
        assert hasattr(apxctrl.model, "ListResponse")
        assert hasattr(apxctrl.model, "RunSequenceRequest")
        assert hasattr(apxctrl.model, "RunSequenceResponse")
        assert hasattr(apxctrl.model, "GetResultRequest")
        assert hasattr(apxctrl.model, "GetResultResponse")

    def test_import_controller(self):
        """Test that controller.py can be imported."""
        import apxctrl.controller
        
        # Verify key classes exist
        assert hasattr(apxctrl.controller, "APxController")

    def test_import_main(self):
        """Test that main.py can be imported."""
        import apxctrl.server
        
        # Verify Flask app exists
        assert hasattr(apxctrl.server, "app")
        assert hasattr(apxctrl.server, "get_state")
        assert hasattr(apxctrl.server, "get_controller")


class TestModels:
    """Test model instantiation."""

    def test_apx_state_enum(self):
        """Test APxState enum values."""
        from apxctrl.model import APxState
        
        assert APxState.NOT_RUNNING.value == "not_running"
        assert APxState.STARTING.value == "starting"
        assert APxState.IDLE.value == "idle"
        assert APxState.RUNNING_STEP.value == "running_step"
        assert APxState.ERROR.value == "error"

    def test_server_state_defaults(self):
        """Test ServerState default values."""
        from apxctrl.model import APxState, ServerState
        
        state = ServerState()
        assert state.apx_state == APxState.NOT_RUNNING
        assert state.project is None
        assert state.apx_pid is None
        assert state.last_error is None

    def test_measurement_info(self):
        """Test MeasurementInfo model."""
        from apxctrl.model import MeasurementInfo
        
        m = MeasurementInfo(index=0, name="Test Measurement", checked=True)
        assert m.index == 0
        assert m.name == "Test Measurement"
        assert m.checked is True

    def test_signal_path_info(self):
        """Test SignalPathInfo model."""
        from apxctrl.model import MeasurementInfo, SignalPathInfo
        
        sp = SignalPathInfo(
            index=0,
            name="Test Signal Path",
            checked=True,
            measurements=[
                MeasurementInfo(index=0, name="M1", checked=True),
                MeasurementInfo(index=1, name="M2", checked=False),
            ],
        )
        assert sp.index == 0
        assert sp.name == "Test Signal Path"
        assert len(sp.measurements) == 2

    def test_sequence_info(self):
        """Test SequenceInfo model."""
        from apxctrl.model import MeasurementInfo, SequenceInfo, SignalPathInfo
        
        seq = SequenceInfo(
            index=0,
            name="Test Sequence",
            signal_paths=[
                SignalPathInfo(
                    index=0,
                    name="SP1",
                    checked=True,
                    measurements=[MeasurementInfo(index=0, name="M1", checked=True)],
                ),
            ],
        )
        assert seq.index == 0
        assert seq.name == "Test Sequence"
        assert len(seq.signal_paths) == 1

    def test_list_response(self):
        """Test ListResponse model."""
        from apxctrl.model import APxState, ListResponse
        
        resp = ListResponse(
            success=True,
            message="OK",
            apx_state=APxState.IDLE,
            total_sequences=1,
            total_signal_paths=2,
            total_measurements=5,
        )
        assert resp.success is True
        assert resp.total_sequences == 1

    def test_run_sequence_request(self):
        """Test RunSequenceRequest model."""
        from apxctrl.model import RunSequenceRequest
        
        req = RunSequenceRequest(sequence_name="My Sequence", test_run_id="TR-001")
        assert req.sequence_name == "My Sequence"
        assert req.test_run_id == "TR-001"

    def test_run_sequence_response(self):
        """Test RunSequenceResponse model."""
        from apxctrl.model import APxState, RunSequenceResponse
        
        resp = RunSequenceResponse(
            success=True,
            message="Passed",
            sequence_name="My Sequence",
            test_run_id="TR-001",
            passed=True,
            duration_seconds=5.5,
            apx_state=APxState.IDLE,
        )
        assert resp.passed is True
        assert resp.duration_seconds == 5.5

    def test_get_result_request(self):
        """Test GetResultRequest model."""
        from apxctrl.model import GetResultRequest
        
        req = GetResultRequest(
            test_run_id="TR-001",
            results_path="C:\\Users\\user\\output",
        )
        assert req.test_run_id == "TR-001"
        assert req.results_path == "C:\\Users\\user\\output"

    def test_get_result_response(self):
        """Test GetResultResponse model."""
        from apxctrl.model import GetResultResponse
        
        resp = GetResultResponse(
            success=True,
            message="Found results",
            test_run_id="TR-001",
            directory_found="TR-001_20260108",
            zip_size_bytes=12345,
        )
        assert resp.success is True
        assert resp.directory_found == "TR-001_20260108"


class TestController:
    """Test APxController without actual APx connection."""

    def test_controller_init(self):
        """Test APxController initialization."""
        from apxctrl.controller import APxController
        from apxctrl.model import APxState, ServerState
        
        state = ServerState()
        controller = APxController(state)
        
        assert controller._state is state
        assert controller._apx_instance is None
        assert controller._clr_initialized is False

    def test_controller_check_health_not_running(self):
        """Test check_health when APx is not running."""
        from apxctrl.controller import APxController
        from apxctrl.model import APxState, ServerState
        
        state = ServerState()
        state.apx_state = APxState.NOT_RUNNING
        controller = APxController(state)
        
        # Should return True (healthy) when not running
        assert controller.check_health() is True

    def test_controller_list_structure_not_initialized(self):
        """Test list_structure when APx is not initialized."""
        from apxctrl.controller import APxController
        from apxctrl.model import ServerState
        
        state = ServerState()
        controller = APxController(state)
        
        sequences, active, error = controller.list_structure()
        assert sequences == []
        assert active is None
        assert error == "APx instance not initialized"

    def test_controller_run_sequence_not_initialized(self):
        """Test run_sequence when APx is not initialized."""
        from apxctrl.controller import APxController
        from apxctrl.model import ServerState
        
        state = ServerState()
        controller = APxController(state)
        
        passed, duration, error = controller.run_sequence("Test")
        assert passed is False
        assert duration == 0.0
        assert error == "APx instance not initialized"


class TestFlaskApp:
    """Test Flask app endpoints."""

    @pytest.fixture
    def client(self):
        """Create a test client."""
        import apxctrl.server
        apxctrl.server.app.config["TESTING"] = True
        with apxctrl.server.app.test_client() as client:
            yield client

    def test_index(self, client):
        """Test index endpoint."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.get_json()
        assert data["service"] == "APx Control Server"
        assert "endpoints" in data

    def test_health(self, client):
        """Test health endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.get_json()
        assert "apx_state" in data
        assert "uptime_seconds" in data

    def test_status(self, client):
        """Test status endpoint."""
        response = client.get("/status")
        assert response.status_code == 200
        data = response.get_json()
        assert "apx_state" in data
        assert "server_started_at" in data

    def test_reset(self, client):
        """Test reset endpoint."""
        response = client.post("/reset")
        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "killed_processes" in data

    def test_list_not_running(self, client):
        """Test list endpoint when APx not running."""
        response = client.get("/list")
        assert response.status_code == 409  # Conflict - APx not running
        data = response.get_json()
        assert data["success"] is False

    def test_run_sequence_not_running(self, client):
        """Test run-sequence endpoint when APx not running."""
        response = client.post(
            "/run-sequence",
            json={"sequence_name": "Test", "test_run_id": ""},
        )
        assert response.status_code == 409  # Conflict - APx not ready
        data = response.get_json()
        assert data["success"] is False

    def test_get_result_not_found(self, client):
        """Test get-result endpoint with non-existent path."""
        response = client.post(
            "/get-result",
            json={"test_run_id": "TR-999", "results_path": "C:\\nonexistent\\path"},
        )
        assert response.status_code == 404
        data = response.get_json()
        assert data["success"] is False

    def test_setup_no_file(self, client):
        """Test setup endpoint without file."""
        response = client.post("/setup")
        assert response.status_code == 400
        data = response.get_json()
        assert data["success"] is False
        assert "No file provided" in data["message"]

