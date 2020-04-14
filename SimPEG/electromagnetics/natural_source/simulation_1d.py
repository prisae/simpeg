import numpy as np
import scipy as sp
import properties
from scipy.constants import mu_0

from ...utils import mkvc, sdiag, Zero
from ..base import BaseEMSimulation
from ...data import Data
from ... import props

from .survey import Survey1D

from discretize import TensorMesh


class BaseSimulation1D(BaseEMSimulation):

    # Must be 1D survey object
    survey = properties.Instance(
        "a Survey1D survey object", Survey1D, required=True
    )

    sensitivity_method = properties.StringChoice(
        "Choose 1st or 2nd order computations with sensitivity matrix ('1st_order', '2nd_order')", {
            "1st_order": [],
            "2nd_order": []
        }
    )

    # Instantiate
    def __init__(self, sensitivity_method="1st_order", **kwargs):
        
        self.sensitivity_method = sensitivity_method

        BaseEMSimulation.__init__(self, **kwargs)


    # Compute layer admittances for a 1d model
    def _get_admittance(self, f, sigma_1d):

        """
        Layer admittances

        :param np.float f: frequency in Hz
        :pamam np.array sig: layer conductivities in S/m (nLayers,)
        :return a: layer admittances (nLayers,)
        """

        return (1 - 1j)*np.sqrt(sigma_1d/(4*np.pi*f*mu_0))


    def _get_propagator_matricies_1d(self, src, thicknesses, sigma_1d):
        """
        For a given source and layered Earth model, this returns the list of
        propagator matricies.

        :param SimPEG.electromagnetics.sources.AnalyticPlanewave1D src: Analytic 1D planewave source
        :param np.array thicknesses: layer thicknesses (nLayers-1,)
        :param np.array sigma_1d: layer conductivities (nLayers,)
        :return list Q: list containing matrix for each layer [nLayers,]
        """
        
        # Get frequency for planewave source
        f = src.frequency

        # Admittance for all layers
        a = self._get_admittance(f, sigma_1d)

        # Compute exponent terms
        e_neg = np.exp(-(1 + 1j)*np.sqrt(np.pi*mu_0*f*sigma_1d[0:-1])*thicknesses)
        e_pos = np.exp( (1 + 1j)*np.sqrt(np.pi*mu_0*f*sigma_1d[0:-1])*thicknesses)

        Q = [(0.5/a[jj])*np.array([
                [a[jj]*(e_neg[jj] + e_pos[jj]), (e_neg[jj] - e_pos[jj])],
                [a[jj]**2*(e_neg[jj] - e_pos[jj]), a[jj]*(e_neg[jj] + e_pos[jj])]
                ], dtype=complex) for jj in range(0, len(thicknesses))]

        # Compute 2x2 matrix for bottom layer and append
        Q.append(np.array([[1, 1], [a[-1], -a[-1]]], dtype=complex))

        return Q


    def _get_sigma_derivative_matricies_1d(self, src, thicknesses, sigma_1d):
        """
        For a given source (frequency) and layered Earth, return the list containing
        the derivative of each layer's propagator matrix with respect to conductivity.
        
        :param SimPEG.electromagnetics.sources.AnalyticPlanewave1D src: Analytic 1D planewave source
        :param np.array thicknesses: layer thicknesses (nLayers-1,)
        :param np.array sigma_1d: layer conductivities (nLayers,)
        :return list dQdig: list containing matrix for each layer [nLayers,]
        """
        
        # Get frequency for planewave source
        f = src.frequency

        # Admittance for all layers
        a = self._get_admittance(f, sigma_1d)

        # Compute exponent terms
        e_neg = np.exp(-(1 + 1j)*np.sqrt(np.pi*mu_0*f*sigma_1d[0:-1])*thicknesses)
        e_pos = np.exp( (1 + 1j)*np.sqrt(np.pi*mu_0*f*sigma_1d[0:-1])*thicknesses)

        # product of i, wavenumber and layer thicknesses
        ikh = (1 + 1j)*np.sqrt(np.pi*mu_0*f*sigma_1d[0:-1])*thicknesses  

        dQdsig = [np.array([
            [
            ikh[jj]*(-e_neg[jj] + e_pos[jj])/(4*sigma_1d[jj]),
            (-1/(4*sigma_1d[jj]*a[jj]))*(e_neg[jj] - e_pos[jj]) - (thicknesses[jj]/(4*a[jj]**2))*(e_neg[jj] + e_pos[jj])
            ],[
            (a[jj]/(4*sigma_1d[jj]))*(e_neg[jj] - e_pos[jj]) - (thicknesses[jj]/4)*(e_neg[jj] + e_pos[jj]),
            ikh[jj]*(-e_neg[jj] + e_pos[jj])/(4*sigma_1d[jj])]
            ], dtype=complex) for jj in range(0, len(thicknesses))]

        # Compute 2x2 matrix for bottom layer
        dQdsig.append(np.array([[0, 0], [0.5*a[-1]/sigma_1d[-1], -0.5*a[-1]/sigma_1d[-1]]], dtype=complex))

        return dQdsig

    def _get_thicknesses_derivative_matricies_1d(self, src, thicknesses, sigma_1d):
        """
        For a given source (frequency) and layered Earth, return the list containing
        the derivative of each layer's propagator matrix with respect to thickness.
        
        :param SimPEG.electromagnetics.sources.AnalyticPlanewave1D src: Analytic 1D planewave source
        :param np.array thicknesses: layer thicknesses (nLayers-1,)
        :param np.array sigma_1d: layer conductivities (nLayers,)
        :return list dQdig: list containing matrix for each layer [nLayers-1,]
        """
        
        # Get frequency for planewave source
        f = src.frequency

        # Admittance for all layers
        a = self._get_admittance(f, sigma_1d)

        # Compute exponent terms
        e_neg = np.exp(-(1 + 1j)*np.sqrt(np.pi*mu_0*f*sigma_1d[0:-1])*thicknesses)
        e_pos = np.exp( (1 + 1j)*np.sqrt(np.pi*mu_0*f*sigma_1d[0:-1])*thicknesses)

        C = 1j*np.pi*f*mu_0  # Constant

        # Create dQdh matrix for each layer
        dQdh = [C*np.array([
                [a[jj]*(-e_neg[jj] + e_pos[jj]), (-e_neg[jj] - e_pos[jj])],
                [a[jj]**2*(-e_neg[jj] - e_pos[jj]), a[jj]*(-e_neg[jj] + e_pos[jj])]
                ], dtype=complex) for jj in range(0, len(thicknesses))]

        return dQdh


    def _compute_dMdsig_jj(self, Q, dQdsig, jj):
        """
        Combine propagator matricies
        """

        if len(Q) > 1:
            return np.linalg.multi_dot(Q[0:jj] + [dQdsig[jj]] + Q[jj+1:])
        else:
            return dQdsig[0]



class Simulation1DLayers(BaseSimulation1D):

    """
    Simulation class for the 1D MT problem using propagator matrix solution.
    """

    # Add layer thickness as invertible property
    thicknesses, thicknessesMap, thicknessesDeriv = props.Invertible(
        "thicknesses of the layers"
    )

    # Store sensitivity
    _Jmatrix = None
    fix_Jmatrix = False
    storeJ = properties.Bool("store the sensitivity", default=False)

    @property
    def deleteTheseOnModelUpdate(self):
        toDelete = super(Simulation1DLayers, self).deleteTheseOnModelUpdate
        if self.fix_Jmatrix:
            return toDelete

        if self._Jmatrix is not None:
            toDelete += ['_Jmatrix']
        return toDelete

    # Instantiate
    def __init__(self, **kwargs):
        BaseSimulation1D.__init__(self, **kwargs)


    def fields(self, m):
        """
        Compute the complex impedance for a given model.

        :param np.array m: inversion model (nP,)
        :return f: complex impedances
        """

        if m is not None:
            self.model = m

        f = []

        # For each source
        for source_ii in self.survey.source_list:

            # Propagator matricies for all layers
            Q = self._get_propagator_matricies_1d(
                    source_ii, self.thicknesses, self.sigma
                    )

            # Create final matix
            if len(Q) > 1:
                M = np.linalg.multi_dot(Q)
            else:
                M = Q[0]

            # Compute complex impedance
            complex_impedance = M[0, 1]/M[1, 1]

            # For each receiver, compute datum
            for rx in source_ii.receiver_list:
                if rx.component is 'real':
                    f.append(complex_impedance.real)
                elif rx.component is 'imag':
                    f.append(complex_impedance.imag)
                elif rx.component is 'app_res':
                    f.append(np.abs(complex_impedance)**2/(2*np.pi*source_ii.frequency*mu_0))

        return np.array(f)


    def dpred(self, m=None, f=None):
        """
        Predict data vector for a given model.

        :param np.array m: inversion model (nP,)
        :return d: data vector
        """

        if f is None:
            if m is None:
                m = self.model
            f = self.fields(m)

        return f


    def getJ(self, m, f=None, sensitivity_method=None):

        """
        Compute and store the sensitivity matrix.

        :param numpy.ndarray m: inversion model (nP,)
        :param String method: Choose from '1st_order' or '2nd_order'
        :return: J (ndata, nP)
        """
        
        if sensitivity_method == None:
            sensitivity_method = self.sensitivity_method

        if self._Jmatrix is not None:

            pass

        elif sensitivity_method == '1st_order':

            # 1st order computation
            self.model = m

            N = self.survey.nD
            M = self.model.size
            Jmatrix = np.zeros((N, M), dtype=float, order='F')

            factor = 0.001
            for ii in range(0, len(m)):
                m1 = m.copy()
                m2 = m.copy()
                dm = np.max([factor*np.abs(m[ii]), 1e-6])
                m1[ii] = m[ii] - 0.5*dm
                m2[ii] = m[ii] + 0.5*dm
                d1 = self.dpred(m1)
                d2 = self.dpred(m2)
                Jmatrix[:, ii] = (d2 - d1)/dm 

            self._Jmatrix = Jmatrix

        elif sensitivity_method == '2nd_order':

            self.model = m

            # 2nd order computation
            N = self.survey.nD
            M = self.model.size
            Jmatrix = np.zeros((N, M), dtype=float, order='F')

            # Sensitivity of parameters with model
            if self.thicknessesMap == None:
                dMdm = self.sigmaDeriv
            else:
                dMdm = sp.sparse.vstack([self.sigmaDeriv, self.thicknessesDeriv])

            # Loop over each source (frequency)
            COUNT = 0
            for source_ii in self.survey.source_list:

                # Get Propagator matricies
                Q = self._get_propagator_matricies_1d(
                        source_ii, self.thicknesses, self.sigma
                        )

                # Create product of propagator matricies
                if len(Q) > 1:
                    M = np.linalg.multi_dot(Q)
                else:
                    M = Q[0]

                # Get sigma derivative matricies
                dQdsig = self._get_sigma_derivative_matricies_1d(
                        source_ii, self.thicknesses, self.sigma
                        )

                dMdsig = np.zeros((4, len(self.sigma)), dtype=float)

                for jj in range(0, len(self.sigma)):
                    if len(Q) > 1:
                        dMdsig_jj =  np.linalg.multi_dot(Q[0:jj] + [dQdsig[jj]] + Q[jj+1:])
                    else:
                        dMdsig_jj =  dQdsig[0]

                    dMdsig[:, jj] = np.r_[
                        dMdsig_jj[0, 1].real,
                        dMdsig_jj[0, 1].imag,
                        dMdsig_jj[1, 1].real,
                        dMdsig_jj[1, 1].imag
                        ]

                # Get h derivative matricies
                if self.thicknessesMap != None:

                    dQdh = self._get_thicknesses_derivative_matricies_1d(
                            source_ii, self.thicknesses, self.sigma
                            )

                    dMdh = np.zeros((4, len(self.thicknesses)), dtype=float)
                    for jj in range(0, len(self.thicknesses)):
                        dMdh_jj = np.linalg.multi_dot(Q[0:jj] + [dQdh[jj]] + Q[jj+1:])
                        dMdh[:, jj] = np.r_[
                            dMdh_jj[0, 1].real,
                            dMdh_jj[0, 1].imag,
                            dMdh_jj[1, 1].real,
                            dMdh_jj[1, 1].imag
                            ]

                # Compute for each receiver
                for rx in source_ii.receiver_list:
                    if rx.component is 'real':
                        C = 2*(M[0, 1].real*M[1, 1].real + M[0, 1].imag*M[1, 1].imag)/np.abs(M[1, 1])**2
                        A = (
                            np.abs(M[1, 1])**-2*np.c_[
                                M[1, 1].real,
                                M[1, 1].imag,
                                M[0, 1].real-C*M[1, 1].real,
                                M[0, 1].imag-C*M[1, 1].imag
                                ]
                            )
                    elif rx.component is 'imag':
                        C = 2*(-M[0, 1].real*M[1, 1].imag + M[0, 1].imag*M[1, 1].real)/np.abs(M[1, 1])**2
                        A = (
                            np.abs(M[1, 1])**-2*np.c_[
                                -M[1, 1].imag,
                                M[1, 1].real,
                                M[0, 1].imag-C*M[1, 1].real,
                                -M[0, 1].real-C*M[1, 1].imag
                                ]
                            )
                    elif rx.component is 'app_res':
                        rho_a = np.abs(M[0, 1]/M[1, 1])**2/(2*np.pi*source_ii.frequency*mu_0)
                        A = (
                            2*np.abs(M[1, 1])**-2*np.c_[
                                M[0, 1].real/(2*np.pi*source_ii.frequency*mu_0),
                                M[0, 1].imag/(2*np.pi*source_ii.frequency*mu_0),
                                -rho_a*M[1, 1].real,
                                -rho_a*M[1, 1].imag
                                ]
                            )

                    # Compute row of sensitivity
                    if self.thicknessesMap == None:
                        Jmatrix[COUNT, :] = np.dot(A, dMdsig)*dMdm
                    else:
                        Jmatrix[COUNT, :] = np.dot(A, np.hstack([dMdsig, dMdh]))*dMdm

                    COUNT = COUNT + 1

            self._Jmatrix = Jmatrix

        return self._Jmatrix


    def Jvec(self, m, v, f=None, sensitivity_method=None):
        """
        Sensitivity times a vector.

        :param numpy.ndarray m: inversion model (nP,)
        :param numpy.ndarray v: vector which we take sensitivity product with
            (nP,)
        :param String method: Choose from 'approximate' or 'analytic'
        :return: Jv (ndata,)
        """

        if sensitivity_method == None:
            sensitivity_method = self.sensitivity_method

        J = self.getJ(m, f=None, sensitivity_method=sensitivity_method)

        return mkvc(np.dot(J, v))


    def Jtvec(self, m, v, f=None, sensitivity_method=None):
        """
        Transpose of sensitivity times a vector.

        :param numpy.ndarray m: inversion model (nP,)
        :param numpy.ndarray v: vector which we take sensitivity product with
            (nD,)
        :param String method: Choose from 'approximate' or 'analytic'
        :return: Jtv (nP,)
        """

        if sensitivity_method == None:
            sensitivity_method = self.sensitivity_method

        J = self.getJ(m, f=None, sensitivity_method=sensitivity_method)

        return mkvc(np.dot(v, J))


# class SimulationPseudo3D(BaseSimulation1D):

#     """
#     Simulation class for the pseudo 3D MT problem using propagator matrix solution.
#     """

#     # tensor mesh
#     mesh = properties.Instance("a discretize Tensor mesh instance", TensorMesh)

#     # Topography active cells
#     indActive = properties.Array('Topography active cells', dtype=bool)

#     # Instantiate
#     def __init__(self, **kwargs):
#         BaseSimulation1D.__init__(self, **kwargs)



#     def get_1D_model_for_receiver(self):



#     def fields(self, m):
#         """
#         Compute the complex impedance for a given model.

#         :param np.array m: inversion model (nP,)
#         :return f: complex impedances
#         """

#         if m is not None:
#             self.model = m

#         f = []

#         # For each source
#         for source_ii in self.survey.source_list:

#             # We can parallelize this
#             Q = self._get_propagator_matricies_1d(
#                     source_ii, self.thicknesses, self.sigma
#                     )

#             # Create final matix
#             if len(Q) > 1:
#                 M = np.linalg.multi_dot(Q)
#             else:
#                 M = Q[0]

#             # Add to fields
#             f.append(M[0, 1]/M[1, 1])

#         return f


#     def dpred(self, m=None, f=None):
#         """
#         Predict data vector for a given model.

#         :param np.array m: inversion model (nP,)
#         :return d: data vector
#         """

#         if f is None:
#             if m is None:
#                 m = self.model
#             f = self.fields(m)

#         d = []

#         # For each source and receiver, compute the datum.
#         for ii in range(0, len(self.survey.source_list)):
#             src = self.survey.source_list[ii]
#             for rx in src.receiver_list:
#                 if rx.component is 'real':
#                     d.append(f[ii].real)
#                 elif rx.component is 'imag':
#                     d.append(f[ii].imag)
#                 elif rx.component is 'app_res':
#                     d.append(np.abs(f[ii])**2/(2*np.pi*src.frequency*mu_0))
       
#         return mkvc(np.hstack(d))

        