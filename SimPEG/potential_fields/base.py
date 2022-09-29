import os
import discretize
import properties
import numpy as np
import multiprocessing
from ..simulation import LinearSimulation
from scipy.sparse import csr_matrix as csr
from SimPEG.utils import mkvc

###############################################################################
#                                                                             #
#                             Base Potential Fields Simulation                   #
#                                                                             #
###############################################################################


class BasePFSimulation(LinearSimulation):
    actInd = properties.Array(
        "Array of active cells (ground)", dtype=(bool, int), default=None
    )

    n_cpu = properties.Integer(
        "Number of processors used for the forward simulation",
        default=int(multiprocessing.cpu_count()),
    )

    store_sensitivities = properties.StringChoice(
        "Compute and store G", choices=["disk", "ram", "forward_only"], default="ram"
    )

    def __init__(self, mesh, **kwargs):
        super().__init__(mesh, **kwargs)

        # Find non-zero cells
        if getattr(self, "actInd", None) is not None:
            if self.actInd.dtype == "bool":
                indices = np.where(self.actInd)[0]
            else:
                indices = self.actInd
        else:
            indices = np.asarray(range(self.mesh.nC))

        self.nC = len(indices)

        if isinstance(mesh, discretize.TensorMesh):
            nodes = mesh.nodes
            inds = np.arange(mesh.n_nodes).reshape(mesh.shape_nodes, order="F")
            if mesh.dim == 2:
                cell_nodes = [
                    inds[:-1, :-1].reshape(-1, order="F"),
                    inds[1:, :-1].reshape(-1, order="F"),
                    inds[:-1, 1:].reshape(-1, order="F"),
                    inds[1:, 1:].reshape(-1, order="F"),
                ]
            if mesh.dim == 3:
                cell_nodes = [
                    inds[:-1, :-1, :-1].reshape(-1, order="F"),
                    inds[1:, :-1, :-1].reshape(-1, order="F"),
                    inds[:-1, 1:, :-1].reshape(-1, order="F"),
                    inds[1:, 1:, :-1].reshape(-1, order="F"),
                    inds[:-1, :-1, 1:].reshape(-1, order="F"),
                    inds[1:, :-1, 1:].reshape(-1, order="F"),
                    inds[:-1, 1:, 1:].reshape(-1, order="F"),
                    inds[1:, 1:, 1:].reshape(-1, order="F"),
                ]
            cell_nodes = np.stack(cell_nodes, axis=-1)[indices]
        elif isinstance(mesh, discretize.TreeMesh):
            nodes = np.r_[mesh.nodes, mesh.hanging_nodes]
            cell_nodes = mesh.cell_nodes[indices]
        else:
            raise ValueError("Mesh must be 3D tensor or Octree.")
        unique, unique_inv = np.unique(cell_nodes.T, return_inverse=True)
        self._nodes = nodes[unique]  # unique active nodes
        self._unique_inv = unique_inv.reshape(cell_nodes.T.shape)

    def linear_operator(self):
        if self.store_sensitivities == "disk":
            sens_name = self.sensitivity_path + "sensitivity.npy"
            if os.path.exists(sens_name):
                # do not pull array completely into ram, just need to check the size
                kernel = np.load(sens_name, mmap_mode="r")
                if kernel.shape == (self.survey.nD, self.nC):
                    print(f"Found sensitivity file at {sens_name} with expected shape")
                    kernel = np.asarray(kernel)
                    return kernel
        # multiprocessed
        with multiprocessing.pool.Pool() as pool:
            kernel = pool.starmap(
                self.evaluate_integral, self.survey._location_component_iterator()
            )
        if self.store_sensitivities != "forward_only":
            kernel = np.vstack(kernel)
        else:
            kernel = np.concatenate(kernel)
        if self.store_sensitivities == "disk":
            print(f"writing sensitivity to {sens_name}")
            os.makedirs(self.sensitivity_path, exist_ok=True)
            np.save(sens_name, kernel)
        return kernel

    def evaluate_integral(self):
        """
        evaluate_integral

        Compute the forward linear relationship between the model and the physics at a point.
        :param self:
        :return:
        """

        raise RuntimeError(
            f"Integral calculations must implemented by the subclass {self}."
        )

    @property
    def forwardOnly(self):
        """The forwardOnly property has been removed. Please set the store_sensitivites
        property instead.
        """
        raise TypeError(
            "The forwardOnly property has been removed. Please set the store_sensitivites "
            "property instead."
        )

    @forwardOnly.setter
    def forwardOnly(self, other):
        raise TypeError(
            "The forwardOnly property has been removed. Please set the store_sensitivites "
            "property instead."
        )

    @property
    def parallelized(self):
        """The parallelized property has been removed. If interested, try out
        loading dask for parallelism by doing ``import SimPEG.dask``.
        """
        raise TypeError(
            "parallelized has been removed. If interested, try out "
            "loading dask for parallelism by doing ``import SimPEG.dask``. "
        )

    @parallelized.setter
    def parallelized(self, other):
        raise TypeError(
            "Do not set parallelized. If interested, try out "
            "loading dask for parallelism by doing ``import SimPEG.dask``."
        )

    @property
    def n_cpu(self):
        """The parallelized property has been removed. If interested, try out
        loading dask for parallelism by doing ``import SimPEG.dask``.
        """
        raise TypeError(
            "n_cpu has been removed. If interested, try out "
            "loading dask for parallelism by doing ``import SimPEG.dask``."
        )

    @parallelized.setter
    def n_cpu(self, other):
        raise TypeError(
            "Do not set n_cpu. If interested, try out "
            "loading dask for parallelism by doing ``import SimPEG.dask``."
        )


class BaseEquivalentSourceLayerSimulation(BasePFSimulation):
    """Base equivalent source layer simulation class

    Parameters
    ----------
    mesh : discretize.BaseMesh
        A 2D tensor or tree mesh defining discretization along the x and y directions
    cell_z_top : numpy.ndarray or float
        Define the elevations for the top face of all cells in the layer
    cell_z_bottom : numpy.ndarray or float
        Define the elevations for the bottom face of all cells in the layer

    """

    def __init__(self, mesh, cell_z_top, cell_z_bottom, **kwargs):

        if mesh.dim != 2:
            raise AttributeError("Mesh to equivalent source layer must be 2D.")

        super().__init__(mesh, **kwargs)

        if isinstance(cell_z_top, (int, float)):
            cell_z_top = float(cell_z_top) * np.ones(mesh.nC)

        if isinstance(cell_z_bottom, (int, float)):
            cell_z_bottom = float(cell_z_bottom) * np.ones(mesh.nC)

        if (mesh.nC != len(cell_z_top)) | (mesh.nC != len(cell_z_bottom)):
            raise AttributeError(
                "'cell_z_top' and 'cell_z_bottom' must have length equal to number of cells."
            )

        all_nodes = self._nodes[self._unique_inv]
        all_nodes = [
            np.c_[all_nodes[0], cell_z_bottom],
            np.c_[all_nodes[1], cell_z_bottom],
            np.c_[all_nodes[2], cell_z_bottom],
            np.c_[all_nodes[3], cell_z_bottom],
            np.c_[all_nodes[0], cell_z_top],
            np.c_[all_nodes[1], cell_z_top],
            np.c_[all_nodes[2], cell_z_top],
            np.c_[all_nodes[3], cell_z_top],
        ]
        self._nodes = np.stack(all_nodes, axis=0)
        self._unique_inv = None


def progress(iter, prog, final):
    """
    progress(iter,prog,final)
    Function measuring the progress of a process and print to screen the %.
    Useful to estimate the remaining runtime of a large problem.
    Created on Dec, 20th 2015
    @author: dominiquef
    """
    arg = np.floor(float(iter) / float(final) * 10.0)

    if arg > prog:

        print("Done " + str(arg * 10) + " %")
        prog = arg

    return prog


def get_dist_wgt(mesh, receiver_locations, actv, R, R0):
    """
    get_dist_wgt(xn,yn,zn,receiver_locations,R,R0)

    Function creating a distance weighting function required for the magnetic
    inverse problem.

    INPUT
    xn, yn, zn : Node location
    receiver_locations       : Observation locations [obsx, obsy, obsz]
    actv        : Active cell vector [0:air , 1: ground]
    R           : Decay factor (mag=3, grav =2)
    R0          : Small factor added (default=dx/4)

    OUTPUT
    wr       : [nC] Vector of distance weighting

    Created on Dec, 20th 2015

    @author: dominiquef
    """

    # Find non-zero cells
    if actv.dtype == "bool":
        inds = (
            np.asarray([inds for inds, elem in enumerate(actv, 1) if elem], dtype=int)
            - 1
        )
    else:
        inds = actv

    nC = len(inds)

    # Create active cell projector
    P = csr((np.ones(nC), (inds, range(nC))), shape=(mesh.nC, nC))

    # Geometrical constant
    p = 1 / np.sqrt(3)

    # Create cell center location
    Ym, Xm, Zm = np.meshgrid(mesh.vectorCCy, mesh.vectorCCx, mesh.vectorCCz)
    hY, hX, hZ = np.meshgrid(mesh.hy, mesh.hx, mesh.hz)

    # Remove air cells
    Xm = P.T * mkvc(Xm)
    Ym = P.T * mkvc(Ym)
    Zm = P.T * mkvc(Zm)

    hX = P.T * mkvc(hX)
    hY = P.T * mkvc(hY)
    hZ = P.T * mkvc(hZ)

    V = P.T * mkvc(mesh.vol)
    wr = np.zeros(nC)

    ndata = receiver_locations.shape[0]
    count = -1
    print("Begin calculation of distance weighting for R= " + str(R))

    for dd in range(ndata):

        nx1 = (Xm - hX * p - receiver_locations[dd, 0]) ** 2
        nx2 = (Xm + hX * p - receiver_locations[dd, 0]) ** 2

        ny1 = (Ym - hY * p - receiver_locations[dd, 1]) ** 2
        ny2 = (Ym + hY * p - receiver_locations[dd, 1]) ** 2

        nz1 = (Zm - hZ * p - receiver_locations[dd, 2]) ** 2
        nz2 = (Zm + hZ * p - receiver_locations[dd, 2]) ** 2

        R1 = np.sqrt(nx1 + ny1 + nz1)
        R2 = np.sqrt(nx1 + ny1 + nz2)
        R3 = np.sqrt(nx2 + ny1 + nz1)
        R4 = np.sqrt(nx2 + ny1 + nz2)
        R5 = np.sqrt(nx1 + ny2 + nz1)
        R6 = np.sqrt(nx1 + ny2 + nz2)
        R7 = np.sqrt(nx2 + ny2 + nz1)
        R8 = np.sqrt(nx2 + ny2 + nz2)

        temp = (
            (R1 + R0) ** -R
            + (R2 + R0) ** -R
            + (R3 + R0) ** -R
            + (R4 + R0) ** -R
            + (R5 + R0) ** -R
            + (R6 + R0) ** -R
            + (R7 + R0) ** -R
            + (R8 + R0) ** -R
        )

        wr = wr + (V * temp / 8.0) ** 2.0

        count = progress(dd, count, ndata)

    wr = np.sqrt(wr) / V
    wr = mkvc(wr)
    wr = np.sqrt(wr / (np.max(wr)))

    print("Done 100% ...distance weighting completed!!\n")

    return wr
