from __future__ import annotations

import re

import discretize
import numpy as np
import pytest
from scipy.sparse import diags
from geoana.em.static import MagneticPrism
from scipy.constants import mu_0
from scipy.sparse.linalg import LinearOperator, aslinearoperator

import simpeg
from simpeg import maps, utils
from simpeg.utils import model_builder
from simpeg.potential_fields import magnetics as mag


def get_block_inds(grid: np.ndarray, block: np.ndarray) -> np.ndarray:
    """
    Get the indices for a block

    Parameters
    ----------
    grid : np.ndarray
        (n, 3) array of xyz locations
    block : np.ndarray
        (3, 2) array of (xmin, xmax), (ymin, ymax), (zmin, zmax) dimensions of
        the block.

    Returns
    -------
    np.ndarray
        boolean array of indices corresponding to the block
    """

    return np.where(
        (grid[:, 0] > block[0, 0])
        & (grid[:, 0] < block[0, 1])
        & (grid[:, 1] > block[1, 0])
        & (grid[:, 1] < block[1, 1])
        & (grid[:, 2] > block[2, 0])
        & (grid[:, 2] < block[2, 1])
    )


def create_block_model(
    mesh: discretize.TensorMesh,
    blocks: tuple[np.ndarray, ...],
    block_params: tuple[float, ...] | tuple[np.ndarray, ...],
) -> tuple[np.ndarray, np.ndarray]:
    """
    Create a magnetic model from a sequence of blocks

    Parameters
    ----------
    mesh : discretize.TensorMesh
        TensorMesh object to put the model on
    blocks : Tuple[np.ndarray, ...]
        Tuple of block definitions (each element is (3, 2) array of
        (xmin, xmax), (ymin, ymax), (zmin, zmax) dimensions of the block)
    block_params : Tuple[float, ...]
        Tuple of parameters to assign for each block. Must be the same length
        as ``blocks``.

    Returns
    -------
    Tuple[np.ndarray, np.ndarray]
        Tuple of the magnetic model and active_cells (a boolean array)

    Raises
    ------
    ValueError
        if ``blocks`` and ``block_params`` have incompatible dimensions
    """
    if len(blocks) != len(block_params):
        raise ValueError(
            "'blocks' and 'block_params' must have the same number of elements"
        )
    model = np.zeros((mesh.n_cells, np.atleast_1d(block_params[0]).shape[0]))
    for block, params in zip(blocks, block_params):
        block_ind = get_block_inds(mesh.cell_centers, block)
        model[block_ind] = params
    active_cells = np.any(np.abs(model) > 0, axis=1)
    return model.squeeze(), active_cells


def create_mag_survey(
    components: list[str],
    receiver_locations: np.ndarray,
    inducing_field_params: tuple[float, float, float],
) -> mag.Survey:
    """
    create a magnetic Survey

    Parameters
    ----------
    components : List[str]
        List of components to model
    receiver_locations : np.ndarray
        (n, 3) array of xyz receiver locations
    inducing_field_params : Tuple[float, float, float]
        amplitude, inclination, and declination of the inducing field

    Returns
    -------
    mag.Survey
        a magnetic Survey instance
    """
    receivers = mag.Point(receiver_locations, components=components)
    strenght, inclination, declination = inducing_field_params
    source_field = mag.UniformBackgroundField(
        receiver_list=[receivers],
        amplitude=strenght,
        inclination=inclination,
        declination=declination,
    )
    return mag.Survey(source_field)


def get_shifted_locations(
    receiver_locations: np.ndarray, delta: float, direction: str
) -> tuple[np.ndarray, np.ndarray]:
    """
    Shift the locations of receivers along a particular direction.
    """
    if direction == "x":
        index = 0
    elif direction == "y":
        index = 1
    elif direction == "z":
        index = 2
    else:
        raise ValueError(f"Invalid direction '{direction}'")
    plus, minus = receiver_locations.copy(), receiver_locations.copy()
    plus[:, index] += delta / 2.0
    minus[:, index] -= delta / 2.0
    return plus, minus


class TestsMagSimulation:
    """
    Test mag simulation against the analytic solutions single prisms
    """

    @pytest.fixture
    def mag_mesh(self) -> discretize.TensorMesh:
        """
        a small tensor mesh for testing magnetic simulations

        Returns
        -------
        discretize.TensorMesh
            the tensor mesh for testing
        """
        # Define a mesh
        cs = 0.2
        hxind = [(cs, 41)]
        hyind = [(cs, 41)]
        hzind = [(cs, 41)]
        mesh = discretize.TensorMesh([hxind, hyind, hzind], "CCC")
        return mesh

    @pytest.fixture
    def two_blocks(self) -> tuple[np.ndarray, np.ndarray]:
        """
        The parameters defining two blocks.

        The boundaries of the prism should match nodes in the mesh, otherwise
        these blocks won't be exactly represented in the mesh model.

        Returns
        -------
        Tuple[np.ndarray, np.ndarray]
            Tuple of (3, 2) arrays of (xmin, xmax), (ymin, ymax), (zmin, zmax)
            dimensions of each block.
        """
        block1 = np.array([[-2.5, 0.5], [-3.1, 1.3], [-3.7, 1.5]])
        block2 = np.array([[0.7, 1.9], [-0.7, 2.7], [-1.7, 0.7]])
        return block1, block2

    @pytest.fixture
    def receiver_locations(self) -> np.ndarray:
        """
        a grid of receivers for testing

        Returns
        -------
        np.ndarray
            (n, 3) array of receiver locations
        """
        # Create plane of observations
        nx, ny = 5, 5
        xr = np.linspace(-20, 20, nx)
        yr = np.linspace(-20, 20, ny)
        X, Y = np.meshgrid(xr, yr)
        Z = np.ones_like(X) * 3.0
        return np.c_[X.reshape(-1), Y.reshape(-1), Z.reshape(-1)]

    @pytest.fixture
    def inducing_field(
        self,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """
        inducing field

        Return inducing field as amplitude and angles and as vector components.

        Returns
        -------
        tuple[tuple[float, float, float], tuple[float, float, float]]
            (amplitude, inclination, declination), (b_x, b_y, b_z)
        """
        h0_amplitude, h0_inclination, h0_declination = (50000.0, 60.0, 250.0)
        b0 = mag.analytics.IDTtoxyz(-h0_inclination, h0_declination, h0_amplitude)
        return (h0_amplitude, h0_inclination, h0_declination), b0

    @pytest.mark.parametrize(
        "engine,parallel_kwargs",
        [
            ("geoana", {"n_processes": None}),
            ("geoana", {"n_processes": 1}),
            ("choclo", {"numba_parallel": False}),
            ("choclo", {"numba_parallel": True}),
        ],
        ids=["geoana_serial", "geoana_parallel", "choclo_serial", "choclo_parallel"],
    )
    @pytest.mark.parametrize("store_sensitivities", ("ram", "disk", "forward_only"))
    def test_magnetic_field_and_tmi_w_susceptibility(
        self,
        engine,
        parallel_kwargs,
        store_sensitivities,
        tmp_path,
        mag_mesh,
        two_blocks,
        receiver_locations,
        inducing_field,
    ):
        """
        Test forwarding the magnetic field and tmi (with susceptibility as model)
        """
        inducing_field_params, b0 = inducing_field

        chi1 = 0.01
        chi2 = 0.02
        model, active_cells = create_block_model(mag_mesh, two_blocks, (chi1, chi2))
        model_reduced = model[active_cells]
        # Create reduced identity map for Linear Problem
        identity_map = maps.IdentityMap(nP=int(sum(active_cells)))

        survey = create_mag_survey(
            components=["bx", "by", "bz", "tmi"],
            receiver_locations=receiver_locations,
            inducing_field_params=inducing_field_params,
        )

        sim = mag.Simulation3DIntegral(
            mag_mesh,
            survey=survey,
            chiMap=identity_map,
            active_cells=active_cells,
            sensitivity_path=str(tmp_path / f"{engine}"),
            store_sensitivities=store_sensitivities,
            engine=engine,
            **parallel_kwargs,
        )

        data = sim.dpred(model_reduced)
        d_x = data[0::4]
        d_y = data[1::4]
        d_z = data[2::4]
        d_t = data[3::4]

        # Compute analytical response from magnetic prism
        block1, block2 = two_blocks
        prism_1 = MagneticPrism(block1[:, 0], block1[:, 1], chi1 * b0 / mu_0)
        prism_2 = MagneticPrism(block2[:, 0], block2[:, 1], chi2 * b0 / mu_0)

        d = prism_1.magnetic_flux_density(
            receiver_locations
        ) + prism_2.magnetic_flux_density(receiver_locations)

        # TMI projection
        tmi = sim.tmi_projection
        d_t2 = d_x * tmi[0] + d_y * tmi[1] + d_z * tmi[2]

        # Check results
        rtol, atol = 2e-6, 1e-6
        np.testing.assert_allclose(
            d_t, d_t2, rtol=rtol, atol=atol
        )  # double check internal projection
        np.testing.assert_allclose(d_x, d[:, 0], rtol=rtol, atol=atol)
        np.testing.assert_allclose(d_y, d[:, 1], rtol=rtol, atol=atol)
        np.testing.assert_allclose(d_z, d[:, 2], rtol=rtol, atol=atol)
        np.testing.assert_allclose(d_t, d @ tmi, rtol=rtol, atol=atol)

    @pytest.mark.parametrize(
        "engine, parallel_kwargs",
        [
            ("geoana", {"n_processes": None}),
            ("geoana", {"n_processes": 1}),
            ("choclo", {"numba_parallel": False}),
            ("choclo", {"numba_parallel": True}),
        ],
        ids=["geoana_serial", "geoana_parallel", "choclo_serial", "choclo_parallel"],
    )
    @pytest.mark.parametrize("store_sensitivities", ("ram", "disk", "forward_only"))
    def test_magnetic_gradiometry_w_susceptibility(
        self,
        engine,
        parallel_kwargs,
        store_sensitivities,
        tmp_path,
        mag_mesh,
        two_blocks,
        receiver_locations,
        inducing_field,
    ):
        """
        Test magnetic gradiometry components (with susceptibility as model)
        """
        inducing_field_params, b0 = inducing_field
        chi1 = 0.01
        chi2 = 0.02
        model, active_cells = create_block_model(mag_mesh, two_blocks, (chi1, chi2))
        model_reduced = model[active_cells]
        # Create reduced identity map for Linear Problem
        identity_map = maps.IdentityMap(nP=int(sum(active_cells)))

        survey = create_mag_survey(
            components=["bxx", "bxy", "bxz", "byy", "byz", "bzz"],
            receiver_locations=receiver_locations,
            inducing_field_params=inducing_field_params,
        )
        sim = mag.Simulation3DIntegral(
            mag_mesh,
            survey=survey,
            chiMap=identity_map,
            active_cells=active_cells,
            sensitivity_path=str(tmp_path / f"{engine}"),
            store_sensitivities=store_sensitivities,
            engine=engine,
            **parallel_kwargs,
        )
        data = sim.dpred(model_reduced)
        d_xx = data[0::6]
        d_xy = data[1::6]
        d_xz = data[2::6]
        d_yy = data[3::6]
        d_yz = data[4::6]
        d_zz = data[5::6]

        # Compute analytical response from magnetic prism
        block1, block2 = two_blocks
        prism_1 = MagneticPrism(block1[:, 0], block1[:, 1], chi1 * b0 / mu_0)
        prism_2 = MagneticPrism(block2[:, 0], block2[:, 1], chi2 * b0 / mu_0)

        d = (
            prism_1.magnetic_field_gradient(receiver_locations)
            + prism_2.magnetic_field_gradient(receiver_locations)
        ) * mu_0

        # Check results
        rtol, atol = 5e-7, 1e-6
        np.testing.assert_allclose(d_xx, d[..., 0, 0], rtol=rtol, atol=atol)
        np.testing.assert_allclose(d_xy, d[..., 0, 1], rtol=rtol, atol=atol)
        np.testing.assert_allclose(d_xz, d[..., 0, 2], rtol=rtol, atol=atol)
        np.testing.assert_allclose(d_yy, d[..., 1, 1], rtol=rtol, atol=atol)
        np.testing.assert_allclose(d_yz, d[..., 1, 2], rtol=rtol, atol=atol)
        np.testing.assert_allclose(d_zz, d[..., 2, 2], rtol=rtol, atol=atol)

    @pytest.mark.parametrize(
        "engine, parallel_kwargs",
        [
            ("geoana", {"n_processes": None}),
            ("geoana", {"n_processes": 1}),
            ("choclo", {"numba_parallel": False}),
            ("choclo", {"numba_parallel": True}),
        ],
        ids=["geoana_serial", "geoana_parallel", "choclo_serial", "choclo_parallel"],
    )
    @pytest.mark.parametrize("store_sensitivities", ("ram", "disk", "forward_only"))
    def test_tmi_derivatives_w_susceptibility(
        self,
        engine,
        parallel_kwargs,
        store_sensitivities,
        tmp_path,
        mag_mesh,
        two_blocks,
        receiver_locations,
        inducing_field,
    ):
        """
        Test TMI derivatives (with susceptibility as model)
        """
        (h0_amplitude, h0_inclination, h0_declination), b0 = inducing_field
        chi1 = 0.01
        chi2 = 0.02
        model, active_cells = create_block_model(mag_mesh, two_blocks, (chi1, chi2))
        model_reduced = model[active_cells]
        # Create reduced identity map for Linear Problem
        identity_map = maps.IdentityMap(nP=int(sum(active_cells)))

        components = ["tmi_x", "tmi_y", "tmi_z"]
        survey = create_mag_survey(
            components=components,
            receiver_locations=receiver_locations,
            inducing_field_params=(h0_amplitude, h0_inclination, h0_declination),
        )
        sim = mag.Simulation3DIntegral(
            mag_mesh,
            survey=survey,
            chiMap=identity_map,
            active_cells=active_cells,
            sensitivity_path=str(tmp_path / f"{engine}"),
            store_sensitivities=store_sensitivities,
            engine=engine,
            **parallel_kwargs,
        )
        data = sim.dpred(model_reduced).reshape(-1, len(components))
        tmi_x = data[:, 0]
        tmi_y = data[:, 1]
        tmi_z = data[:, 2]

        # Compute analytical response from magnetic prism
        block1, block2 = two_blocks
        prism_1 = MagneticPrism(block1[:, 0], block1[:, 1], chi1 * b0 / mu_0)
        prism_2 = MagneticPrism(block2[:, 0], block2[:, 1], chi2 * b0 / mu_0)

        d = (
            prism_1.magnetic_field_gradient(receiver_locations)
            + prism_2.magnetic_field_gradient(receiver_locations)
        ) * mu_0

        # Check results
        rtol, atol = 5e-7, 1e-6
        expected_tmi_x = (
            d[:, 0, 0] * b0[0] + d[:, 0, 1] * b0[1] + d[:, 0, 2] * b0[2]
        ) / h0_amplitude
        expected_tmi_y = (
            d[:, 1, 0] * b0[0] + d[:, 1, 1] * b0[1] + d[:, 1, 2] * b0[2]
        ) / h0_amplitude
        expected_tmi_z = (
            d[:, 2, 0] * b0[0] + d[:, 2, 1] * b0[1] + d[:, 2, 2] * b0[2]
        ) / h0_amplitude
        np.testing.assert_allclose(tmi_x, expected_tmi_x, rtol=rtol, atol=atol)
        np.testing.assert_allclose(tmi_y, expected_tmi_y, rtol=rtol, atol=atol)
        np.testing.assert_allclose(tmi_z, expected_tmi_z, rtol=rtol, atol=atol)

    @pytest.mark.parametrize(
        "engine, parallel_kwargs",
        [
            ("geoana", {"n_processes": None}),
            ("geoana", {"n_processes": 1}),
            ("choclo", {"numba_parallel": False}),
            ("choclo", {"numba_parallel": True}),
        ],
        ids=["geoana_serial", "geoana_parallel", "choclo_serial", "choclo_parallel"],
    )
    @pytest.mark.parametrize("store_sensitivities", ("ram", "disk", "forward_only"))
    def test_magnetic_vector_and_tmi_w_magnetization(
        self,
        engine,
        parallel_kwargs,
        store_sensitivities,
        tmp_path,
        mag_mesh,
        two_blocks,
        receiver_locations,
        inducing_field,
    ):
        """
        Test magnetic vector and TMI (using magnetization vectors as model)
        """
        inducing_field_params, b0 = inducing_field
        M1 = (utils.mat_utils.dip_azimuth2cartesian(45, -40) * 0.05).squeeze()
        M2 = (utils.mat_utils.dip_azimuth2cartesian(120, 32) * 0.1).squeeze()

        model, active_cells = create_block_model(mag_mesh, two_blocks, (M1, M2))
        model_reduced = model[active_cells].reshape(-1, order="F")
        # Create reduced identity map for Linear Problem
        identity_map = maps.IdentityMap(nP=int(sum(active_cells)) * 3)

        survey = create_mag_survey(
            components=["bx", "by", "bz", "tmi"],
            receiver_locations=receiver_locations,
            inducing_field_params=inducing_field_params,
        )

        sim = mag.Simulation3DIntegral(
            mag_mesh,
            survey=survey,
            chiMap=identity_map,
            active_cells=active_cells,
            sensitivity_path=str(tmp_path / f"{engine}"),
            store_sensitivities=store_sensitivities,
            model_type="vector",
            engine=engine,
            **parallel_kwargs,
        )

        data = sim.dpred(model_reduced).reshape(-1, 4)

        # Compute analytical response from magnetic prism
        block1, block2 = two_blocks
        prism_1 = MagneticPrism(
            block1[:, 0], block1[:, 1], M1 * np.linalg.norm(b0) / mu_0
        )
        prism_2 = MagneticPrism(
            block2[:, 0], block2[:, 1], M2 * np.linalg.norm(b0) / mu_0
        )

        d = prism_1.magnetic_flux_density(
            receiver_locations
        ) + prism_2.magnetic_flux_density(receiver_locations)
        tmi = sim.tmi_projection

        # Check results
        rtol, atol = 5e-7, 1e-6
        np.testing.assert_allclose(data[:, 0], d[:, 0], rtol=rtol, atol=atol)
        np.testing.assert_allclose(data[:, 1], d[:, 1], rtol=rtol, atol=atol)
        np.testing.assert_allclose(data[:, 2], d[:, 2], rtol=rtol, atol=atol)
        np.testing.assert_allclose(data[:, 3], d @ tmi, rtol=rtol, atol=atol)

    @pytest.mark.parametrize(
        "engine, parallel_kwargs",
        [
            ("geoana", {"n_processes": None}),
            ("geoana", {"n_processes": 1}),
            ("choclo", {"numba_parallel": False}),
            ("choclo", {"numba_parallel": True}),
        ],
        ids=["geoana_serial", "geoana_parallel", "choclo_serial", "choclo_parallel"],
    )
    @pytest.mark.parametrize("store_sensitivities", ("ram", "disk", "forward_only"))
    def test_tmi_derivatives_w_magnetization(
        self,
        engine,
        parallel_kwargs,
        store_sensitivities,
        tmp_path,
        mag_mesh,
        two_blocks,
        receiver_locations,
        inducing_field,
    ):
        """
        Test TMI derivatives (using magnetization vectors as model)
        """
        (h0_amplitude, h0_inclination, h0_declination), b0 = inducing_field
        M1 = (utils.mat_utils.dip_azimuth2cartesian(45, -40) * 0.05).squeeze()
        M2 = (utils.mat_utils.dip_azimuth2cartesian(120, 32) * 0.1).squeeze()

        model, active_cells = create_block_model(mag_mesh, two_blocks, (M1, M2))
        model_reduced = model[active_cells].reshape(-1, order="F")
        # Create reduced identity map for Linear Problem
        identity_map = maps.IdentityMap(nP=int(sum(active_cells)) * 3)

        components = ["tmi_x", "tmi_y", "tmi_z"]
        survey = create_mag_survey(
            components=components,
            receiver_locations=receiver_locations,
            inducing_field_params=(h0_amplitude, h0_inclination, h0_declination),
        )

        sim = mag.Simulation3DIntegral(
            mag_mesh,
            survey=survey,
            chiMap=identity_map,
            active_cells=active_cells,
            sensitivity_path=str(tmp_path / f"{engine}"),
            store_sensitivities=store_sensitivities,
            model_type="vector",
            engine=engine,
            **parallel_kwargs,
        )

        data = sim.dpred(model_reduced).reshape(-1, len(components))
        tmi_x = data[:, 0]
        tmi_y = data[:, 1]
        tmi_z = data[:, 2]

        # Compute analytical response from magnetic prism
        block1, block2 = two_blocks
        prism_1 = MagneticPrism(
            block1[:, 0], block1[:, 1], M1 * np.linalg.norm(b0) / mu_0
        )
        prism_2 = MagneticPrism(
            block2[:, 0], block2[:, 1], M2 * np.linalg.norm(b0) / mu_0
        )

        d = (
            prism_1.magnetic_field_gradient(receiver_locations)
            + prism_2.magnetic_field_gradient(receiver_locations)
        ) * mu_0

        # Check results
        rtol, atol = 5e-7, 1e-6
        expected_tmi_x = (
            d[:, 0, 0] * b0[0] + d[:, 0, 1] * b0[1] + d[:, 0, 2] * b0[2]
        ) / h0_amplitude
        expected_tmi_y = (
            d[:, 1, 0] * b0[0] + d[:, 1, 1] * b0[1] + d[:, 1, 2] * b0[2]
        ) / h0_amplitude
        expected_tmi_z = (
            d[:, 2, 0] * b0[0] + d[:, 2, 1] * b0[1] + d[:, 2, 2] * b0[2]
        ) / h0_amplitude
        np.testing.assert_allclose(tmi_x, expected_tmi_x, rtol=rtol, atol=atol)
        np.testing.assert_allclose(tmi_y, expected_tmi_y, rtol=rtol, atol=atol)
        np.testing.assert_allclose(tmi_z, expected_tmi_z, rtol=rtol, atol=atol)

    @pytest.mark.parametrize(
        "engine, parallel_kwargs",
        [
            ("geoana", {"n_processes": None}),
            ("geoana", {"n_processes": 1}),
            ("choclo", {"numba_parallel": False}),
            ("choclo", {"numba_parallel": True}),
        ],
        ids=["geoana_serial", "geoana_parallel", "choclo_serial", "choclo_parallel"],
    )
    @pytest.mark.parametrize("store_sensitivities", ("ram", "disk", "forward_only"))
    def test_magnetic_field_amplitude_w_magnetization(
        self,
        engine,
        parallel_kwargs,
        store_sensitivities,
        tmp_path,
        mag_mesh,
        two_blocks,
        receiver_locations,
        inducing_field,
    ):
        """
        Test magnetic field amplitude (using magnetization vectors as model)
        """
        inducing_field_params, b0 = inducing_field
        M1 = (utils.mat_utils.dip_azimuth2cartesian(45, -40) * 0.05).squeeze()
        M2 = (utils.mat_utils.dip_azimuth2cartesian(120, 32) * 0.1).squeeze()

        model, active_cells = create_block_model(mag_mesh, two_blocks, (M1, M2))
        model_reduced = model[active_cells].reshape(-1, order="F")
        # Create reduced identity map for Linear Problem
        identity_map = maps.IdentityMap(nP=int(sum(active_cells)) * 3)

        survey = create_mag_survey(
            components=["bx", "by", "bz"],
            receiver_locations=receiver_locations,
            inducing_field_params=inducing_field_params,
        )

        sim = mag.Simulation3DIntegral(
            mag_mesh,
            survey=survey,
            chiMap=identity_map,
            active_cells=active_cells,
            sensitivity_path=str(tmp_path / f"{engine}"),
            store_sensitivities=store_sensitivities,
            model_type="vector",
            is_amplitude_data=True,
            engine=engine,
            **parallel_kwargs,
        )

        data = sim.dpred(model_reduced)

        # Compute analytical response from magnetic prism
        block1, block2 = two_blocks
        prism_1 = MagneticPrism(
            block1[:, 0], block1[:, 1], M1 * np.linalg.norm(b0) / mu_0
        )
        prism_2 = MagneticPrism(
            block2[:, 0], block2[:, 1], M2 * np.linalg.norm(b0) / mu_0
        )

        d = prism_1.magnetic_flux_density(
            receiver_locations
        ) + prism_2.magnetic_flux_density(receiver_locations)
        d_amp = np.linalg.norm(d, axis=1)

        # Check results
        rtol, atol = 5e-7, 1e-6
        np.testing.assert_allclose(data, d_amp, rtol=rtol, atol=atol)

    @pytest.mark.parametrize("engine", ("geoana", "choclo"))
    @pytest.mark.parametrize("direction", ("x", "y", "z"))
    def test_tmi_derivatives_finite_diff(
        self,
        engine,
        direction,
        mag_mesh,
        two_blocks,
        receiver_locations,
        inducing_field,
    ):
        """
        Test tmi derivatives against finite differences.

        Use float64 elements in the sensitivity matrix to avoid numerical
        instabilities due to small values of delta.
        """
        # Get inducing field and two blocks model
        inducing_field_params, b0 = inducing_field
        chi1, chi2 = 0.01, 0.02
        model, active_cells = create_block_model(mag_mesh, two_blocks, (chi1, chi2))
        model_reduced = model[active_cells]
        identity_map = maps.IdentityMap(nP=int(sum(active_cells)))
        # Create survey to compute tmi derivative through analytic solution
        survey = create_mag_survey(
            components=f"tmi_{direction}",
            receiver_locations=receiver_locations,
            inducing_field_params=inducing_field_params,
        )
        kwargs = dict(
            chiMap=identity_map,
            active_cells=active_cells,
            engine=engine,
            sensitivity_dtype=np.float64,
        )
        simulation = mag.Simulation3DIntegral(
            mag_mesh,
            survey=survey,
            **kwargs,
        )
        # Create shifted surveys to compute tmi derivatives through finite differences
        delta = 1e-6
        shifted_surveys = [
            create_mag_survey(
                components="tmi",
                receiver_locations=shifted_locations,
                inducing_field_params=inducing_field_params,
            )
            for shifted_locations in get_shifted_locations(
                receiver_locations, delta, direction
            )
        ]
        simulations_tmi = [
            mag.Simulation3DIntegral(mag_mesh, survey=shifted_survey, **kwargs)
            for shifted_survey in shifted_surveys
        ]
        # Compute tmi derivatives
        tmi_derivative = simulation.dpred(model_reduced)
        # Compute tmi derivatives with finite differences
        tmis = [sim.dpred(model_reduced) for sim in simulations_tmi]
        tmi_derivative_finite_diff = (tmis[0] - tmis[1]) / delta
        # Compare results
        rtol, atol = 1e-6, 5e-6
        np.testing.assert_allclose(
            tmi_derivative, tmi_derivative_finite_diff, rtol=rtol, atol=atol
        )

    @pytest.mark.parametrize("engine", ("choclo", "geoana"))
    @pytest.mark.parametrize("store_sensitivities", ("ram", "disk", "forward_only"))
    def test_sensitivity_dtype(
        self,
        engine,
        store_sensitivities,
        mag_mesh,
        receiver_locations,
        tmp_path,
    ):
        """Test sensitivity_dtype."""
        # Create survey
        receivers = mag.Point(receiver_locations, components="tmi")
        sources = mag.UniformBackgroundField(
            [receivers], amplitude=50_000, inclination=45, declination=10
        )
        survey = mag.Survey(sources)
        # Create reduced identity map for Linear Problem
        active_cells = np.ones(mag_mesh.n_cells, dtype=bool)
        idenMap = maps.IdentityMap(nP=mag_mesh.n_cells)
        # Create simulation
        sensitivity_path = tmp_path
        if engine == "choclo":
            sensitivity_path /= "dummy"
        simulation = mag.Simulation3DIntegral(
            mag_mesh,
            survey=survey,
            chiMap=idenMap,
            active_cells=active_cells,
            engine=engine,
            store_sensitivities=store_sensitivities,
            sensitivity_path=str(sensitivity_path),
        )
        # sensitivity_dtype should be float64 when running forward only,
        # but float32 in other cases
        if store_sensitivities == "forward_only":
            assert simulation.sensitivity_dtype is np.float64
        else:
            assert simulation.sensitivity_dtype is np.float32

    @pytest.mark.parametrize("invalid_dtype", (float, np.float16))
    def test_invalid_sensitivity_dtype_assignment(self, mag_mesh, invalid_dtype):
        """
        Test invalid sensitivity_dtype assignment
        """
        simulation = mag.Simulation3DIntegral(mag_mesh)
        # Check if error is raised
        msg = "sensitivity_dtype must be either np.float32 or np.float64."
        with pytest.raises(TypeError, match=msg):
            simulation.sensitivity_dtype = invalid_dtype

    def test_invalid_engine(self, mag_mesh):
        """Test if error is raised after invalid engine."""
        engine = "invalid engine"
        msg = rf"'engine' must be in \('geoana', 'choclo'\). Got '{engine}'"
        with pytest.raises(ValueError, match=msg):
            mag.Simulation3DIntegral(mag_mesh, engine=engine)

    def test_choclo_and_n_proceesses(self, mag_mesh):
        """Check if warning is raised after passing n_processes with choclo engine."""
        msg = "The 'n_processes' will be ignored when selecting 'choclo'"
        with pytest.warns(UserWarning, match=msg):
            simulation = mag.Simulation3DIntegral(
                mag_mesh, engine="choclo", n_processes=2
            )
        # Check if n_processes was overwritten and set to None
        assert simulation.n_processes is None

    def test_choclo_and_sensitivity_path_as_dir(self, mag_mesh, tmp_path):
        """
        Check if error is raised when sensitivity_path is a dir with choclo engine.
        """
        # Create a sensitivity_path directory
        sensitivity_path = tmp_path / "sensitivity_dummy"
        sensitivity_path.mkdir()
        # Check if error is raised
        msg = re.escape(
            f"The passed sensitivity_path '{str(sensitivity_path)}' is a directory"
        )
        with pytest.raises(ValueError, match=msg):
            mag.Simulation3DIntegral(
                mag_mesh,
                store_sensitivities="disk",
                sensitivity_path=str(sensitivity_path),
                engine="choclo",
            )

    def test_sensitivities_on_disk(self, mag_mesh, receiver_locations, tmp_path):
        """
        Test if sensitivity matrix is correctly being stored in disk when asked
        """
        # Build survey
        survey = create_mag_survey(
            components=["tmi"],
            receiver_locations=receiver_locations,
            inducing_field_params=(50000.0, 20.0, 45.0),
        )
        # Build simulation
        sensitivities_path = tmp_path / "sensitivities"
        simulation = mag.Simulation3DIntegral(
            mesh=mag_mesh,
            survey=survey,
            store_sensitivities="disk",
            sensitivity_path=str(sensitivities_path),
            engine="choclo",
        )
        simulation.G
        # Check if sensitivity matrix was stored in disk and is a memmap
        assert sensitivities_path.is_file()
        assert type(simulation.G) is np.memmap

    def test_sensitivities_on_ram(self, mag_mesh, receiver_locations):
        """
        Test if sensitivity matrix is correctly being allocated in memory when asked
        """
        # Build survey
        survey = create_mag_survey(
            components=["tmi"],
            receiver_locations=receiver_locations,
            inducing_field_params=(50000.0, 20.0, 45.0),
        )
        # Build simulation
        simulation = mag.Simulation3DIntegral(
            mesh=mag_mesh,
            survey=survey,
            store_sensitivities="ram",
            engine="choclo",
        )
        simulation.G
        # Check if sensitivity matrix is a Numpy array (stored in memory)
        assert type(simulation.G) is np.ndarray

    def test_choclo_missing(self, mag_mesh, monkeypatch):
        """
        Check if error is raised when choclo is missing and chosen as engine.
        """
        # Monkeypatch choclo in simpeg.potential_fields.base
        monkeypatch.setattr(simpeg.potential_fields.base, "choclo", None)
        # Check if error is raised
        msg = "The choclo package couldn't be found."
        with pytest.raises(ImportError, match=msg):
            mag.Simulation3DIntegral(mag_mesh, engine="choclo")


def test_removed_modeltype():
    """Test if accesing removed modelType property raises error."""
    h = [[(2, 2)], [(2, 2)], [(2, 2)]]
    mesh = discretize.TensorMesh(h)
    receiver_location = np.array([[0, 0, 100]])
    receiver = mag.Point(receiver_location, components="tmi")
    background_field = mag.UniformBackgroundField(
        receiver_list=[receiver], amplitude=50_000, inclination=90, declination=0
    )
    survey = mag.Survey(background_field)
    mapping = maps.IdentityMap(mesh, nP=mesh.n_cells)
    sim = mag.Simulation3DIntegral(mesh, survey=survey, chiMap=mapping)
    message = "modelType has been removed, please use model_type."
    with pytest.raises(NotImplementedError, match=message):
        sim.modelType


class BaseFixtures:
    """
    Base test class with some fixtures.

    Requires that any child class implements a ``scalar_model`` boolean fixture.
    It can be a standalone fixture, or it can be a class parametrization.
    """

    def build_survey(self, *, components):
        # Observation points
        x = np.linspace(-20.0, 20.0, 4)
        x, y = np.meshgrid(x, x)
        z = 5.0 * np.ones_like(x)
        coordinates = np.vstack((x.ravel(), y.ravel(), z.ravel())).T
        receivers = mag.receivers.Point(coordinates, components=components)
        source_field = mag.UniformBackgroundField(
            receiver_list=[receivers],
            amplitude=55_000,
            inclination=12,
            declination=-35,
        )
        survey = mag.survey.Survey(source_field)
        return survey

    @pytest.fixture(
        params=[
            "tmi",
            ["bx", "by", "bz"],
            ["tmi", "bx"],
            ["tmi_x", "tmi_y", "tmi_z"],
        ],
        ids=["tmi", "mag_components", "tmi_and_mag", "tmi_derivs"],
    )
    def survey(self, request):
        """
        Return sample magnetic survey.
        """
        return self.build_survey(components=request.param)

    @pytest.fixture
    def mesh(self):
        # Mesh
        dh = 5.0
        hx = [(dh, 4)]
        mesh = discretize.TensorMesh([hx, hx, hx], "CCN")
        return mesh

    @pytest.fixture
    def susceptibilities(self, mesh, scalar_model: bool):
        """Create sample susceptibilities."""
        susceptibilities = 1e-10 * np.ones(
            mesh.n_cells if scalar_model else 3 * mesh.n_cells
        )
        ind_sphere = model_builder.get_indices_sphere(
            np.r_[0.0, 0.0, -20.0], 10.0, mesh.cell_centers
        )
        if scalar_model:
            susceptibilities[ind_sphere] = 0.2
        else:
            susceptibilities[: mesh.n_cells][ind_sphere] = 0.2
            susceptibilities[mesh.n_cells : 2 * mesh.n_cells][ind_sphere] = 0.3
            susceptibilities[2 * mesh.n_cells : 3 * mesh.n_cells][ind_sphere] = 0.5
        return susceptibilities


@pytest.mark.parametrize(
    "scalar_model", [True, False], ids=["scalar_model", "vector_model"]
)
class TestnD(BaseFixtures):
    """
    Test the ``nD`` property.
    """

    @pytest.fixture
    def survey_b_norm(self):
        return self.build_survey(components=["bx", "by", "bz"])

    @pytest.fixture
    def mapping(self, mesh, scalar_model):
        nparams = mesh.n_cells if scalar_model else 3 * mesh.n_cells
        return maps.IdentityMap(nP=nparams)

    def test_nD(self, mesh, survey, mapping, susceptibilities, scalar_model):
        """
        Test nD on tmi data.
        """
        model_type = "scalar" if scalar_model else "vector"
        simulation = mag.simulation.Simulation3DIntegral(
            survey=survey,
            mesh=mesh,
            chiMap=mapping,
            store_sensitivities="ram",
            engine="choclo",
            model_type=model_type,
        )
        receivers = survey.source_field.receiver_list
        n_data = sum(rx.locations.shape[0] * len(rx.components) for rx in receivers)
        assert simulation.nD == n_data
        dpred = simulation.dpred(susceptibilities)
        assert dpred.size == simulation.nD

    def test_nD_amplitude_data(
        self, mesh, survey_b_norm, mapping, susceptibilities, scalar_model
    ):
        """
        Test nD on amplitude data.
        """
        model_type = "scalar" if scalar_model else "vector"
        simulation = mag.simulation.Simulation3DIntegral(
            survey=survey_b_norm,
            mesh=mesh,
            chiMap=mapping,
            store_sensitivities="ram",
            engine="choclo",
            model_type=model_type,
            is_amplitude_data=True,
        )
        receivers = survey_b_norm.source_field.receiver_list
        n_data = (
            sum(rx.locations.shape[0] * len(rx.components) for rx in receivers) // 3
        )
        assert simulation.nD == n_data
        dpred = simulation.dpred(susceptibilities)
        assert dpred.size == simulation.nD


@pytest.mark.parametrize(
    "scalar_model", [True, False], ids=["scalar_model", "vector_model"]
)
class TestGLinearOperator(BaseFixtures):
    """
    Test G as a linear operator.
    """

    @pytest.fixture
    def mapping(self, mesh, scalar_model):
        nparams = mesh.n_cells if scalar_model else 3 * mesh.n_cells
        return maps.IdentityMap(nP=nparams)

    @pytest.mark.parametrize("parallel", [True, False], ids=["parallel", "serial"])
    def test_G_dot_m(
        self, survey, mesh, mapping, susceptibilities, scalar_model, parallel
    ):
        """Test G @ m."""
        model_type = "scalar" if scalar_model else "vector"
        simulation, simulation_ram = (
            mag.simulation.Simulation3DIntegral(
                survey=survey,
                mesh=mesh,
                chiMap=mapping,
                store_sensitivities=store,
                engine="choclo",
                numba_parallel=parallel,
                model_type=model_type,
            )
            for store in ("forward_only", "ram")
        )
        assert isinstance(simulation.G, LinearOperator)
        assert isinstance(simulation_ram.G, np.ndarray)

        expected = simulation_ram.G @ susceptibilities

        atol = np.max(np.abs(expected)) * 1e-8
        np.testing.assert_allclose(simulation.G @ susceptibilities, expected, atol=atol)

    @pytest.mark.parametrize("parallel", [True, False], ids=["parallel", "serial"])
    def test_G_t_dot_v(self, survey, mesh, mapping, scalar_model, parallel):
        """Test G.T @ v."""
        model_type = "scalar" if scalar_model else "vector"
        simulation, simulation_ram = (
            mag.simulation.Simulation3DIntegral(
                survey=survey,
                mesh=mesh,
                chiMap=mapping,
                store_sensitivities=store,
                engine="choclo",
                numba_parallel=parallel,
                model_type=model_type,
            )
            for store in ("forward_only", "ram")
        )
        assert isinstance(simulation.G, LinearOperator)
        assert isinstance(simulation_ram.G, np.ndarray)

        vector = np.random.default_rng(seed=42).uniform(size=survey.nD)
        expected = simulation_ram.G.T @ vector

        atol = np.max(np.abs(expected)) * 1e-7
        np.testing.assert_allclose(simulation.G.T @ vector, expected, atol=atol)

    def test_not_implemented(self, survey, mesh, mapping, scalar_model):
        """
        Test NotImplementedError when forward_only and geoana as engine.
        """
        engine, store = "geoana", "forward_only"
        model_type = "scalar" if scalar_model else "vector"
        simulation = mag.simulation.Simulation3DIntegral(
            survey=survey,
            mesh=mesh,
            chiMap=mapping,
            store_sensitivities=store,
            engine=engine,
            model_type=model_type,
        )
        msg = re.escape(
            "Accessing matrix G with "
            'store_sensitivities="forward_only" and engine="geoana" '
            "hasn't been implemented yet."
        )
        with pytest.raises(NotImplementedError, match=msg):
            simulation.G


@pytest.mark.parametrize(
    "scalar_model", [True, False], ids=["scalar_model", "vector_model"]
)
class TestJacobian(BaseFixtures):
    """
    Test methods related to Jacobian matrix in magnetic simulation.
    """

    atol_ratio = 1e-7

    @pytest.fixture(params=["identity_map", "exp_map"])
    def mapping(self, mesh, scalar_model: bool, request):
        nparams = mesh.n_cells if scalar_model else 3 * mesh.n_cells
        mapping = (
            maps.IdentityMap(nP=nparams)
            if request.param == "identity_map"
            else maps.ExpMap(nP=nparams)
        )
        return mapping

    @pytest.mark.parametrize("engine", ["choclo", "geoana"])
    def test_getJ_as_array(
        self, survey, mesh, mapping, susceptibilities, scalar_model, engine
    ):
        """
        Test the getJ method when J is an array in memory.
        """
        model_type = "scalar" if scalar_model else "vector"
        simulation = mag.simulation.Simulation3DIntegral(
            survey=survey,
            mesh=mesh,
            chiMap=mapping,
            store_sensitivities="ram",
            engine=engine,
            model_type=model_type,
        )
        is_identity_map = type(mapping) is maps.IdentityMap
        model = susceptibilities if is_identity_map else np.log(susceptibilities)

        jac = simulation.getJ(model)
        assert isinstance(jac, np.ndarray)
        # With an identity mapping, the jacobian should be the same as G.
        # With an exp mapping, the jacobian should be G @ the mapping derivative.
        expected_jac = (
            simulation.G if is_identity_map else simulation.G @ mapping.deriv(model)
        )
        np.testing.assert_allclose(jac, expected_jac)

    @pytest.mark.parametrize(
        "engine",
        [
            "choclo",
            pytest.param(
                "geoana",
                marks=pytest.mark.xfail(
                    reason="not implemented", raises=NotImplementedError
                ),
            ),
        ],
    )
    @pytest.mark.parametrize("transpose", [False, True], ids=["J @ m", "J.T @ v"])
    def test_getJ_as_linear_operator(
        self, survey, mesh, mapping, susceptibilities, scalar_model, engine, transpose
    ):
        """
        Test the getJ method when J is a linear operator.
        """
        model_type = "scalar" if scalar_model else "vector"
        simulation = mag.simulation.Simulation3DIntegral(
            survey=survey,
            mesh=mesh,
            chiMap=mapping,
            store_sensitivities="forward_only",
            engine=engine,
            model_type=model_type,
        )
        is_identity_map = type(mapping) is maps.IdentityMap
        model = susceptibilities if is_identity_map else np.log(susceptibilities)

        jac = simulation.getJ(model)
        assert isinstance(jac, LinearOperator)
        if transpose:
            vector = np.random.default_rng(seed=42).uniform(size=survey.nD)
            result = jac.T @ vector
            expected_result = mapping.deriv(model).T @ (simulation.G.T @ vector)
        else:
            result = jac @ model
            expected_result = simulation.G @ (mapping.deriv(model).diagonal() * model)
        np.testing.assert_allclose(result, expected_result)

    @pytest.mark.parametrize("engine", ["choclo", "geoana"])
    def test_getJ_not_implemented(
        self, survey, mesh, mapping, susceptibilities, scalar_model, engine
    ):
        model_type = "scalar" if scalar_model else "vector"
        simulation = mag.simulation.Simulation3DIntegral(
            survey=survey,
            mesh=mesh,
            chiMap=mapping,
            store_sensitivities="ram",
            engine=engine,
            model_type=model_type,
            is_amplitude_data=True,
        )
        is_identity_map = type(mapping) is maps.IdentityMap
        model = susceptibilities if is_identity_map else np.log(susceptibilities)
        with pytest.raises(NotImplementedError):
            simulation.getJ(model)

    @pytest.mark.parametrize(
        ("engine", "store_sensitivities"),
        [
            ("choclo", "ram"),
            ("choclo", "forward_only"),
            ("geoana", "ram"),
            pytest.param(
                "geoana",
                "forward_only",
                marks=pytest.mark.xfail(
                    reason="not implemented", raises=NotImplementedError
                ),
            ),
        ],
    )
    def test_Jvec(
        self,
        survey,
        mesh,
        mapping,
        susceptibilities,
        scalar_model,
        engine,
        store_sensitivities,
    ):
        """
        Test the Jvec method.
        """
        model_type = "scalar" if scalar_model else "vector"
        simulation = mag.simulation.Simulation3DIntegral(
            survey=survey,
            mesh=mesh,
            chiMap=mapping,
            store_sensitivities=store_sensitivities,
            engine=engine,
            model_type=model_type,
            sensitivity_dtype=np.float64,
        )
        is_identity_map = type(mapping) is maps.IdentityMap
        model = susceptibilities if is_identity_map else np.log(susceptibilities)

        vector = np.random.default_rng(seed=42).uniform(size=susceptibilities.size)
        result = simulation.Jvec(model, vector)

        expected_jac = (
            simulation.G
            if is_identity_map
            else simulation.G @ aslinearoperator(mapping.deriv(model))
        )
        expected = expected_jac @ vector

        atol = np.max(np.abs(expected)) * self.atol_ratio
        np.testing.assert_allclose(result, expected, atol=atol)

    @pytest.mark.parametrize(
        ("engine", "store_sensitivities"),
        [
            ("choclo", "ram"),
            ("choclo", "forward_only"),
            ("geoana", "ram"),
            pytest.param(
                "geoana",
                "forward_only",
                marks=pytest.mark.xfail(
                    reason="not implemented", raises=NotImplementedError
                ),
            ),
        ],
    )
    def test_Jtvec(
        self,
        survey,
        mesh,
        mapping,
        susceptibilities,
        scalar_model,
        engine,
        store_sensitivities,
    ):
        """
        Test the Jtvec method.
        """
        model_type = "scalar" if scalar_model else "vector"
        simulation = mag.simulation.Simulation3DIntegral(
            survey=survey,
            mesh=mesh,
            chiMap=mapping,
            store_sensitivities=store_sensitivities,
            engine=engine,
            model_type=model_type,
            sensitivity_dtype=np.float64,
        )
        is_identity_map = type(mapping) is maps.IdentityMap
        model = susceptibilities if is_identity_map else np.log(susceptibilities)

        vector = np.random.default_rng(seed=42).uniform(size=survey.nD)
        result = simulation.Jtvec(model, vector)

        expected_jac = (
            simulation.G
            if is_identity_map
            else simulation.G @ aslinearoperator(mapping.deriv(model))
        )
        expected = expected_jac.T @ vector

        atol = np.max(np.abs(result)) * self.atol_ratio
        np.testing.assert_allclose(result, expected, atol=atol)

    @pytest.mark.parametrize(
        "engine",
        [
            "choclo",
            pytest.param(
                "geoana",
                marks=pytest.mark.xfail(
                    reason="not implemented", raises=NotImplementedError
                ),
            ),
        ],
    )
    @pytest.mark.parametrize("method", ["Jvec", "Jtvec"])
    def test_array_vs_linear_operator(
        self, survey, mesh, mapping, susceptibilities, scalar_model, engine, method
    ):
        """
        Test methods when using "ram" and "forward_only".

        They should give the same results.
        """
        model_type = "scalar" if scalar_model else "vector"
        simulation_lo, simulation_ram = (
            mag.simulation.Simulation3DIntegral(
                survey=survey,
                mesh=mesh,
                chiMap=mapping,
                store_sensitivities=store,
                engine=engine,
                model_type=model_type,
                sensitivity_dtype=np.float64,
            )
            for store in ("forward_only", "ram")
        )
        match method:
            case "Jvec":
                vector_size = susceptibilities.size
            case "Jtvec":
                vector_size = survey.nD
            case _:  # pragma: no cover
                raise ValueError(f"Invalid method '{method}'")  # pragma: no cover
        is_identity_map = type(mapping) is maps.IdentityMap
        model = susceptibilities if is_identity_map else np.log(susceptibilities)

        vector = np.random.default_rng(seed=42).uniform(size=vector_size)
        result_lo = getattr(simulation_lo, method)(model, vector)
        result_ram = getattr(simulation_ram, method)(model, vector)
        atol = np.max(np.abs(result_ram)) * self.atol_ratio
        np.testing.assert_allclose(result_lo, result_ram, atol=atol)

    @pytest.mark.parametrize("engine", ["choclo", "geoana"])
    @pytest.mark.parametrize("weights", [True, False])
    def test_getJtJdiag(
        self, survey, mesh, mapping, susceptibilities, scalar_model, engine, weights
    ):
        """
        Test the ``getJtJdiag`` method with G as an array in memory.
        """
        model_type = "scalar" if scalar_model else "vector"
        simulation = mag.simulation.Simulation3DIntegral(
            survey=survey,
            mesh=mesh,
            chiMap=mapping,
            store_sensitivities="ram",
            engine=engine,
            model_type=model_type,
            sensitivity_dtype=np.float64,
        )
        is_identity_map = type(mapping) is maps.IdentityMap
        model = susceptibilities if is_identity_map else np.log(susceptibilities)

        kwargs = {}
        if weights:
            w_matrix = diags(np.random.default_rng(seed=42).uniform(size=survey.nD))
            kwargs = {"W": w_matrix}
        jtj_diag = simulation.getJtJdiag(model, **kwargs)

        expected_jac = (
            simulation.G if is_identity_map else simulation.G @ mapping.deriv(model)
        )
        if weights:
            expected = np.diag(expected_jac.T @ w_matrix.T @ w_matrix @ expected_jac)
        else:
            expected = np.diag(expected_jac.T @ expected_jac)

        atol = np.max(np.abs(jtj_diag)) * self.atol_ratio
        np.testing.assert_allclose(jtj_diag, expected, atol=atol)

    @pytest.mark.parametrize(
        ("engine", "is_amplitude_data"),
        [("geoana", True), ("geoana", False), ("choclo", True)],
        ids=("geoana-amplitude_data", "geoana-regular_data", "choclo-amplitude_data"),
    )
    def test_getJtJdiag_not_implemented(
        self,
        survey,
        mesh,
        mapping,
        susceptibilities,
        scalar_model,
        engine,
        is_amplitude_data,
    ):
        """
        Test NotImplementedErrors on ``getJtJdiag``.
        """
        model_type = "scalar" if scalar_model else "vector"
        simulation = mag.simulation.Simulation3DIntegral(
            survey=survey,
            mesh=mesh,
            chiMap=mapping,
            store_sensitivities="forward_only",
            engine=engine,
            is_amplitude_data=is_amplitude_data,
            model_type=model_type,
        )
        is_identity_map = type(mapping) is maps.IdentityMap
        model = susceptibilities if is_identity_map else np.log(susceptibilities)
        with pytest.raises(NotImplementedError):
            simulation.getJtJdiag(model)

    @pytest.mark.parametrize("parallel", [True, False], ids=("parallel", "serial"))
    def test_getJtJdiag_forward_only(
        self, survey, mesh, mapping, susceptibilities, scalar_model, parallel
    ):
        """
        Test the ``getJtJdiag`` method with ``"forward_only"`` and choclo.
        """
        model_type = "scalar" if scalar_model else "vector"
        simulation, simulation_ram = (
            mag.simulation.Simulation3DIntegral(
                survey=survey,
                mesh=mesh,
                chiMap=mapping,
                store_sensitivities=store,
                engine="choclo",
                numba_parallel=parallel,
                model_type=model_type,
            )
            for store in ("forward_only", "ram")
        )
        is_identity_map = type(mapping) is maps.IdentityMap
        model = susceptibilities if is_identity_map else np.log(susceptibilities)

        expected = simulation_ram.getJtJdiag(model)
        result = simulation.getJtJdiag(model)

        atol = np.max(np.abs(expected)) * 1e-8
        np.testing.assert_allclose(result, expected, atol=atol)

    @pytest.mark.parametrize("engine", ["choclo", "geoana"])
    def test_getJtJdiag_caching(
        self, survey, mesh, mapping, susceptibilities, scalar_model, engine
    ):
        """
        Test the caching behaviour of the ``getJtJdiag`` method.
        """
        model_type = "scalar" if scalar_model else "vector"
        simulation = mag.simulation.Simulation3DIntegral(
            survey=survey,
            mesh=mesh,
            chiMap=mapping,
            store_sensitivities="ram",
            engine=engine,
            model_type=model_type,
        )

        # Get diagonal of J.T @ J without any weight
        is_identity_map = type(mapping) is maps.IdentityMap
        model = susceptibilities if is_identity_map else np.log(susceptibilities)
        jtj_diagonal_1 = simulation.getJtJdiag(model)
        assert hasattr(simulation, "_gtg_diagonal")
        assert hasattr(simulation, "_weights_sha256")
        gtg_diagonal_1 = simulation._gtg_diagonal
        weights_sha256_1 = simulation._weights_sha256

        # Compute it again and make sure we get the same result
        np.testing.assert_allclose(jtj_diagonal_1, simulation.getJtJdiag(model))

        # Get a new diagonal with weights
        weights_matrix = diags(
            np.random.default_rng(seed=42).uniform(size=simulation.survey.nD)
        )
        jtj_diagonal_2 = simulation.getJtJdiag(model, W=weights_matrix)
        assert hasattr(simulation, "_gtg_diagonal")
        assert hasattr(simulation, "_weights_sha256")
        gtg_diagonal_2 = simulation._gtg_diagonal
        weights_sha256_2 = simulation._weights_sha256

        # The two results should be different
        assert not np.array_equal(jtj_diagonal_1, jtj_diagonal_2)
        assert not np.array_equal(gtg_diagonal_1, gtg_diagonal_2)
        assert weights_sha256_1.digest() != weights_sha256_2.digest()


@pytest.mark.parametrize(
    "scalar_model", [True, False], ids=["scalar_model", "vector_model"]
)
class TestJacobianAmplitudeData(BaseFixtures):
    """
    Test Jacobian related methods with ``is_amplitude_data``.
    """

    atol_ratio = 1e-7

    @pytest.fixture
    def survey(self):
        """
        Sample survey with fixed components bx, by, bz.

        These components are assumed when working with ``is_amplitude_data=True``.
        """
        # Observation points
        x = np.linspace(-20.0, 20.0, 4)
        x, y = np.meshgrid(x, x)
        z = 5.0 * np.ones_like(x)
        coordinates = np.vstack((x.ravel(), y.ravel(), z.ravel())).T
        receivers = mag.receivers.Point(coordinates, components=["bx", "by", "bz"])
        source_field = mag.UniformBackgroundField(
            receiver_list=[receivers],
            amplitude=55_000,
            inclination=12,
            declination=-35,
        )
        survey = mag.survey.Survey(source_field)
        return survey

    @pytest.fixture(params=["identity_map", "exp_map"])
    def mapping(self, mesh, scalar_model: bool, request):
        nparams = mesh.n_cells if scalar_model else 3 * mesh.n_cells
        mapping = (
            maps.IdentityMap(nP=nparams)
            if request.param == "identity_map"
            else maps.ExpMap(nP=nparams)
        )
        return mapping

    @pytest.mark.parametrize("engine", ["choclo", "geoana"])
    def test_getJ_not_implemented(
        self, survey, mesh, mapping, susceptibilities, scalar_model, engine
    ):
        """
        Test the getJ method when J is an array in memory.
        """
        model_type = "scalar" if scalar_model else "vector"
        simulation = mag.simulation.Simulation3DIntegral(
            survey=survey,
            mesh=mesh,
            chiMap=mapping,
            store_sensitivities="ram",
            engine=engine,
            model_type=model_type,
            is_amplitude_data=True,
            sensitivity_dtype=np.float64,
        )
        is_identity_map = type(mapping) is maps.IdentityMap
        model = susceptibilities if is_identity_map else np.log(susceptibilities)
        with pytest.raises(NotImplementedError):
            simulation.getJ(model)

    @pytest.mark.parametrize(
        ("engine", "store_sensitivities"),
        [
            ("choclo", "ram"),
            ("choclo", "forward_only"),
            ("geoana", "ram"),
            pytest.param(
                "geoana",
                "forward_only",
                marks=pytest.mark.xfail(
                    reason="not implemented", raises=NotImplementedError
                ),
            ),
        ],
    )
    def test_Jvec(
        self,
        survey,
        mesh,
        mapping,
        susceptibilities,
        scalar_model,
        engine,
        store_sensitivities,
    ):
        r"""
        Test the Jvec method.

        Test the Jvec method through an alternative implementation.
        Define a :math:`f(\chi)` forward model function that returns the norm of the
        magnetic field given the susceptibility values of :math:`\chi`:

        .. math::

            f(\chi)
                = \lvert \mathbf{B} \rvert
                = \sqrt{B_x^2(\chi) + B_y^2(\chi) + B_z^2(\chi)}

        The gradient of :math:`f(\chi)` (jacobian matrix :math:`\mathbf{J}`) can be
        written as:

        .. math::

            \mathbf{J} = \mathbf{J}_x + \mathbf{J}_y + \mathbf{J}_z

        where:

        .. math::

            \mathbf{J}_x =
                \frac{1}{\lvert \mathbf{B} \rvert}
                B_x(\chi)
                \frac{\partial B_x}{\partial \chi}
                \frac{\partial \chi}{\partial \mathbf{m}}

        and :math:`\mathbf{m}` is the model.
        """
        model_type = "scalar" if scalar_model else "vector"
        simulation = mag.simulation.Simulation3DIntegral(
            survey=survey,
            mesh=mesh,
            chiMap=mapping,
            store_sensitivities=store_sensitivities,
            engine=engine,
            model_type=model_type,
            is_amplitude_data=True,
            sensitivity_dtype=np.float64,
        )
        is_identity_map = type(mapping) is maps.IdentityMap
        model = susceptibilities if is_identity_map else np.log(susceptibilities)
        vector = np.random.default_rng(seed=42).uniform(size=susceptibilities.size)
        result = simulation.Jvec(model, vector)

        magnetic_field = simulation.G @ susceptibilities
        bx, by, bz = (magnetic_field[i::3] for i in (0, 1, 2))
        inv_amplitude = 1 / np.sqrt(bx**2 + by**2 + bz**2)

        g_dot_chideriv_v = (
            simulation.G @ aslinearoperator(mapping.deriv(model)) @ vector
        )
        jac_x_dot_v = diags(inv_amplitude) @ diags(bx) @ g_dot_chideriv_v[0::3]
        jac_y_dot_v = diags(inv_amplitude) @ diags(by) @ g_dot_chideriv_v[1::3]
        jac_z_dot_v = diags(inv_amplitude) @ diags(bz) @ g_dot_chideriv_v[2::3]
        expected = jac_x_dot_v + jac_y_dot_v + jac_z_dot_v

        atol = np.max(np.abs(expected)) * self.atol_ratio
        np.testing.assert_allclose(result, expected, atol=atol)

    @pytest.mark.parametrize(
        ("engine", "store_sensitivities"),
        [
            ("choclo", "ram"),
            ("choclo", "forward_only"),
            ("geoana", "ram"),
            pytest.param(
                "geoana",
                "forward_only",
                marks=pytest.mark.xfail(
                    reason="not implemented", raises=NotImplementedError
                ),
            ),
        ],
    )
    def test_Jtvec(
        self,
        survey,
        mesh,
        mapping,
        susceptibilities,
        scalar_model,
        engine,
        store_sensitivities,
    ):
        """
        Test the Jtvec method.

        Test it similarly to Jvec, but computing the transpose of the matrices.
        """
        model_type = "scalar" if scalar_model else "vector"
        simulation = mag.simulation.Simulation3DIntegral(
            survey=survey,
            mesh=mesh,
            chiMap=mapping,
            store_sensitivities=store_sensitivities,
            engine=engine,
            model_type=model_type,
            is_amplitude_data=True,
            sensitivity_dtype=np.float64,
        )
        is_identity_map = type(mapping) is maps.IdentityMap
        model = susceptibilities if is_identity_map else np.log(susceptibilities)

        # Need to set size as survey.nD / 3 because there's a bug in simulation.nD.
        vector = np.random.default_rng(seed=42).uniform(size=survey.nD // 3)
        result = simulation.Jtvec(model, vector)

        magnetic_field = simulation.G @ susceptibilities
        bx, by, bz = (magnetic_field[i::3] for i in (0, 1, 2))
        inv_amplitude = 1 / np.sqrt(bx**2 + by**2 + bz**2)
        v = np.array(
            (
                bx * inv_amplitude * vector,
                by * inv_amplitude * vector,
                bz * inv_amplitude * vector,
            )
        ).T.ravel()  # interleave the values for bx, by, bz
        expected = mapping.deriv(model).T @ (simulation.G.T @ v)

        atol = np.max(np.abs(result)) * self.atol_ratio
        np.testing.assert_allclose(result, expected, atol=atol)
