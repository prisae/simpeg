from __future__ import print_function
from . import Utils
from . import Regularization, DataMisfit, ObjectiveFunction
from . import Maps
import numpy as np
import scipy.sparse as sp
import matplotlib.pyplot as plt
import warnings
from . import Maps
from .PF import Magnetics, MagneticsDriver
from . import Regularization
from . import Mesh
from . import ObjectiveFunction
import copy
#from .Utils import FuzzyGaussianMixtureWithPrior


class InversionDirective(object):
    """InversionDirective"""

    debug = False    #: Print debugging information
    _regPair = [
        Regularization.BaseComboRegularization,
        Regularization.BaseRegularization,
        ObjectiveFunction.ComboObjectiveFunction
    ]
    _dmisfitPair = [
        DataMisfit.BaseDataMisfit,
        ObjectiveFunction.ComboObjectiveFunction
    ]

    def __init__(self, **kwargs):
        Utils.setKwargs(self, **kwargs)

    @property
    def inversion(self):
        """This is the inversion of the InversionDirective instance."""
        return getattr(self, '_inversion', None)

    @inversion.setter
    def inversion(self, i):
        if getattr(self, '_inversion', None) is not None:
            warnings.warn(
                'InversionDirective {0!s} has switched to a new inversion.'
                .format(self.__class__.__name__)
            )
        self._inversion = i

    @property
    def invProb(self):
        return self.inversion.invProb

    @property
    def opt(self):
        return self.invProb.opt

    @property
    def reg(self):
        if getattr(self, '_reg', None) is None:
            self.reg = self.invProb.reg  # go through the setter
        return self._reg

    @reg.setter
    def reg(self, value):
        assert any([isinstance(value, regtype) for regtype in self._regPair]), (
            "Regularization must be in {}, not {}".format(
                self._regPair, type(value)
            )
        )

        if isinstance(value, Regularization.BaseComboRegularization):
            value = 1 * value  # turn it into a combo objective function
        self._reg = value

    @property
    def dmisfit(self):
        if getattr(self, '_dmisfit', None) is None:
            self.dmisfit = self.invProb.dmisfit  # go through the setter
        return self._dmisfit

    @dmisfit.setter
    def dmisfit(self, value):

        assert any([
            isinstance(value, dmisfittype) for dmisfittype in
            self._dmisfitPair
        ]), "Misfit must be in {}, not {}".format(
            self._dmisfitPair, type(value)
        )

        if not isinstance(value, ObjectiveFunction.ComboObjectiveFunction):
            value = 1 * value  # turn it into a combo objective function
        self._dmisfit = value

    @property
    def survey(self):
        """
           Assuming that dmisfit is always a ComboObjectiveFunction,
           return a list of surveys for each dmisfit [survey1, survey2, ... ]
        """
        return [objfcts.survey for objfcts in self.dmisfit.objfcts]

        # else:
        #     return self.dmisfit.survey

    @property
    def prob(self):
        """
           Assuming that dmisfit is always a ComboObjectiveFunction,
           return a list of problems for each dmisfit [prob1, prob2, ...]
        """
        return [objfcts.prob for objfcts in self.dmisfit.objfcts]

    def initialize(self):
        pass

    def endIter(self):
        pass

    def finish(self):
        pass

    def validate(self, directiveList=None):
        return True


class DirectiveList(object):

    dList = None   #: The list of Directives

    def __init__(self, *directives, **kwargs):
        self.dList = []
        for d in directives:
            assert isinstance(d, InversionDirective), (
                'All directives must be InversionDirectives not {}'
                .format(type(d))
            )
            self.dList.append(d)
        Utils.setKwargs(self, **kwargs)

    @property
    def debug(self):
        return getattr(self, '_debug', False)

    @debug.setter
    def debug(self, value):
        for d in self.dList:
            d.debug = value
        self._debug = value

    @property
    def inversion(self):
        """This is the inversion of the InversionDirective instance."""
        return getattr(self, '_inversion', None)

    @inversion.setter
    def inversion(self, i):
        if self.inversion is i:
            return
        if getattr(self, '_inversion', None) is not None:
            warnings.warn(
                '{0!s} has switched to a new inversion.'
                .format(self.__class__.__name__)
            )
        for d in self.dList:
            d.inversion = i
        self._inversion = i

    def call(self, ruleType):
        if self.dList is None:
            if self.debug:
                print('DirectiveList is None, no directives to call!')
            return

        directives = ['initialize', 'endIter', 'finish']
        assert ruleType in directives, (
            'Directive type must be in ["{0!s}"]'
            .format('", "'.join(directives))
        )
        for r in self.dList:
            getattr(r, ruleType)()

    def validate(self):
        [directive.validate(self) for directive in self.dList]
        return True


class BetaEstimate_ByEig(InversionDirective):
    """BetaEstimate"""

    beta0 = None       #: The initial Beta (regularization parameter)
    beta0_ratio = 1e2  #: estimateBeta0 is used with this ratio
    ninit = 1

    def initialize(self):
        """
            The initial beta is calculated by comparing the estimated
            eigenvalues of JtJ and WtW.

            To estimate the eigenvector of **A**, we will use one iteration
            of the *Power Method*:

            .. math::

                \mathbf{x_1 = A x_0}

            Given this (very course) approximation of the eigenvector, we can
            use the *Rayleigh quotient* to approximate the largest eigenvalue.

            .. math::

                \lambda_0 = \\frac{\mathbf{x^\\top A x}}{\mathbf{x^\\top x}}

            We will approximate the largest eigenvalue for both JtJ and WtW,
            and use some ratio of the quotient to estimate beta0.

            .. math::

                \\beta_0 = \gamma \\frac{\mathbf{x^\\top J^\\top J x}}{\mathbf{x^\\top W^\\top W x}}

            :rtype: float
            :return: beta0
        """

        if self.debug:
            print('Calculating the beta0 parameter.')

        m = self.invProb.model
        f = self.invProb.getFields(m, store=True, deleteWarmstart=False)

        ratio = []
        for i in range(self.ninit):
            x0 = np.random.rand(*m.shape)
            t, b = 0, 0
            i_count = 0
            for mult, dmis in zip(self.dmisfit.multipliers, self.dmisfit.objfcts):
                # check if f is list
                if len(self.dmisfit.objfcts) > 1:
                    t += mult * x0.dot(dmis.deriv2(m, x0, f=f[i_count]))
                else:
                    t += mult * x0.dot(dmis.deriv2(m, x0, f=f))
            for mult, reg in zip(self.reg.multipliers, self.reg.objfcts):
                b += mult * x0.dot(reg.deriv2(m, v=x0))
            i_count += 1
            ratio.append(t / b)

        self.ratio = ratio
        self.beta0 = self.beta0_ratio * np.median(ratio)

        self.invProb.beta = self.beta0


class BetaSchedule(InversionDirective):
    """BetaSchedule"""

    coolingFactor = 8.
    coolingRate = 3

    def endIter(self):
        if self.opt.iter > 0 and self.opt.iter % self.coolingRate == 0:
            if self.debug:
                print(
                    'BetaSchedule is cooling Beta. Iteration: {0:d}'
                    .format(self.opt.iter)
                )
            self.invProb.beta /= self.coolingFactor


class TargetMisfit(InversionDirective):

    chifact = 1.
    phi_d_star = None

    @property
    def target(self):
        if getattr(self, '_target', None) is None:
            # the factor of 0.5 is because we do phid = 0.5*|| dpred - dobs||^2
            if self.phi_d_star is None:

                nD = 0
                for survey in self.survey:
                    nD += survey.nD

                self.phi_d_star = 0.5 * nD

            self._target = self.chifact * self.phi_d_star
        return self._target

    @target.setter
    def target(self, val):
        self._target = val

    def endIter(self):
        if self.invProb.phi_d < self.target:
            self.opt.stopNextIteration = True


class SaveEveryIteration(InversionDirective):

    @property
    def name(self):
        if getattr(self, '_name', None) is None:
            self._name = 'InversionModel'
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def fileName(self):
        if getattr(self, '_fileName', None) is None:
            from datetime import datetime
            self._fileName = '{0!s}-{1!s}'.format(
                self.name, datetime.now().strftime('%Y-%m-%d-%H-%M')
            )
        return self._fileName

    @fileName.setter
    def fileName(self, value):
        self._fileName = value


class SaveModelEveryIteration(SaveEveryIteration):
    """SaveModelEveryIteration"""

    def initialize(self):
        print(
            "SimPEG.SaveModelEveryIteration will save your models as: '###-{0!s}.npy'".format(self.fileName))

    def endIter(self):
        np.save('{0:03d}-{1!s}'.format(
            self.opt.iter, self.fileName), self.opt.xc
        )


class SaveUBCModelEveryIteration(SaveEveryIteration):
    """SaveModelEveryIteration"""

    replace = True
    saveComp = True
    mapping = None

    def initialize(self):

        if getattr(self, 'mapping', None) is None:
            return self.mapPair()
        print("SimPEG.SaveModelEveryIteration will save your models" +
              " in UBC format as: '###-{0!s}.sus'".format(self.fileName))

    def endIter(self):

        if not self.replace:
            fileName = self.fileName + "Iter" + str(self.opt.iter)
        else:
            fileName = self.fileName

        count = -1
        for prob, survey, reg in zip(self.prob, self.survey, self.reg.objfcts):

            count += 1

            xc = prob.mapPair() * self.opt.xc

            # Save model
            if not isinstance(prob, Magnetics.MagneticVector):

                Mesh.TensorMesh.writeModelUBC(reg.mesh,
                                              fileName + '.sus', self.mapping * xc)
            else:

                if prob.coordinate_system == 'spherical':
                    vec_xyz = Magnetics.atp2xyz(xc)
                else:
                    vec_xyz = xc

                nC = self.mapping.shape[1]

                vec_x = self.mapping * vec_xyz[:nC]
                vec_y = self.mapping * vec_xyz[nC:2 * nC]
                vec_z = self.mapping * vec_xyz[2 * nC:]

                vec = np.c_[vec_x, vec_y, vec_z]

                m_pst = Magnetics.xyz2pst(vec, self.survey[0].srcField.param)
                m_ind = m_pst.copy()
                m_ind[:, 1:] = 0.
                m_ind = Magnetics.pst2xyz(m_ind, self.survey[0].srcField.param)

                m_rem = m_pst.copy()
                m_rem[:, 0] = 0.
                m_rem = Magnetics.pst2xyz(m_rem, self.survey[0].srcField.param)

                MagneticsDriver.writeVectorUBC(
                    self.prob[0].mesh, fileName + '_VEC.fld', vec)

                if self.saveComp:
                    MagneticsDriver.writeVectorUBC(
                        self.prob[0].mesh, fileName + '_IND.fld', m_ind)
                    MagneticsDriver.writeVectorUBC(
                        self.prob[0].mesh, fileName + '_REM.fld', m_rem)


class SaveOutputEveryIteration(SaveEveryIteration):
    """SaveModelEveryIteration"""

    header = None
    save_txt = True
    beta = None
    phi_d = None
    phi_m = None
    phi_m_small = None
    phi_m_smooth_x = None
    phi_m_smooth_y = None
    phi_m_smooth_z = None
    phi = None

    def endIter(self):
        f = open(self.fileName + '.txt', 'a')
        f.write(' {0:3d} {1:1.4e} {2:1.4e} {3:1.4e} {4:1.4e}\n'.format(
            self.opt.iter, self.invProb.beta, self.invProb.phi_d, self.invProb.phi_m, self.opt.f))
        f.close()

    def initialize(self):
        if self.save_txt is True:
            print(
                "SimPEG.SaveOutputEveryIteration will save your inversion "
                "progress as: '###-{0!s}.txt'".format(self.fileName)
            )
            f = open(self.fileName + '.txt', 'w')
            self.header = "  #     beta     phi_d     phi_m   phi_m_small     phi_m_smoomth_x     phi_m_smoomth_y     phi_m_smoomth_z      phi\n"
            f.write(self.header)
            f.close()

        self.iter = []
        self.beta = []
        self.alphas = []
        self.phi_d = []
        self.phi_m = []
        self.phi_m_small = []
        self.phi_m_smooth_x = []
        self.phi_m_smooth_y = []
        self.phi_m_smooth_z = []
        self.phi = []
        self.phi_dDeriv = []
        self.phi_mDeriv = []
        self.phi_m_small_Deriv = []
        self.phi_m_smooth_x_Deriv = []
        self.phi_m_smooth_y_Deriv = []
        self.phi_m_smooth_z_Deriv = []
        self.m = []
        self.dpred = []
        self.mref = []

    def endIter(self):

        phi_s, phi_x, phi_y, phi_z = 0, 0, 0, 0
        for reg in self.reg.objfcts:
            phi_s += (
                reg.objfcts[0](self.invProb.model) * reg.alpha_s
            )
            phi_x += (
                reg.objfcts[1](self.invProb.model) * reg.alpha_x
            )

            if reg.regmesh.dim == 2:
                phi_y += (
                    reg.objfcts[2](self.invProb.model) * reg.alpha_y
                )
            elif reg.regmesh.dim == 3:
                phi_y += (
                    reg.objfcts[2](self.invProb.model) * reg.alpha_y
                )
                phi_z += (
                    reg.objfcts[3](self.invProb.model) * reg.alpha_z
                )

        self.beta.append(self.invProb.beta)
        self.alphas.append(self.invProb.reg.multipliers)
        self.phi.append(self.opt.f)
        self.phi_d.append(self.invProb.phi_d)
        self.phi_m.append(self.invProb.phi_m)
        self.phi_m_small.append(phi_s)
        self.phi_m_smooth_x.append(phi_x)
        self.phi_m_smooth_y.append(phi_y)
        self.phi_m_smooth_z.append(phi_z)
        self.phi.append(self.opt.f)

        # Initialize the output dict
        outDict = {}
        # Save the data.
        outDict['iter'] = self.iter
        outDict['beta'] = self.beta
        outDict['phi'] = self.phi
        outDict['phi_d'] = self.phi_d
        outDict['phi_m'] = self.phi_m
        outDict['phi_ms'] = self.phi_m_small
        outDict['phi_mx'] = self.phi_m_smooth_x
        outDict['phi_my'] = self.phi_m_smooth_y
        outDict['phi_mz'] = self.phi_m_smooth_z
        outDict['m'] = self.m
        outDict['dpred'] = self.dpred
        outDict['mref'] = self.mref
        outDict['alphas'] = self.alphas
        outDict['phi_d_deriv'] = self.phi_dDeriv
        outDict['phi_m_deriv'] = self.phi_mDeriv
        outDict['phi_ms_deriv'] = self.phi_m_small_Deriv
        outDict['phi_mx_deriv'] = self.phi_m_smooth_x_Deriv
        outDict['phi_my_deriv'] = self.phi_m_smooth_y_Deriv
        outDict['phi_mz_deriv'] = self.phi_m_smooth_z_Deriv
        # Save the file as a npz
        np.savez('{:03d}-{:s}'.format(self.opt.iter, self.fileName), outDict)

        if self.save_txt:
            f = open(self.fileName + '.txt', 'a')
            f.write(
                ' {0:3d} {1:1.4e} {2:1.4e} {3:1.4e} {4:1.4e} {5:1.4e} '
                '{6:1.4e}  {7:1.4e}  {8:1.4e}\n'.format(
                    self.opt.iter,
                    self.beta[self.opt.iter - 1],
                    self.phi_d[self.opt.iter - 1],
                    self.phi_m[self.opt.iter - 1],
                    self.phi_m_small[self.opt.iter - 1],
                    self.phi_m_smooth_x[self.opt.iter - 1],
                    self.phi_m_smooth_y[self.opt.iter - 1],
                    self.phi_m_smooth_z[self.opt.iter - 1],
                    self.phi[self.opt.iter - 1]
                )
            )
            f.close()

    def load_results(self):
        results = np.loadtxt(self.fileName + str(".txt"), comments="#")
        self.beta = results[:, 1]
        self.phi_d = results[:, 2]
        self.phi_m = results[:, 3]
        self.phi_m_small = results[:, 4]
        self.phi_m_smooth_x = results[:, 5]
        self.phi_m_smooth_y = results[:, 6]
        self.phi_m_smooth_z = results[:, 7]

        self.phi_m_smooth = (
            self.phi_m_smooth_x + self.phi_m_smooth_y + self.phi_m_smooth_z
        )

        self.f = results[:, 7]

        self.target_misfit = self.invProb.dmisfit.prob.survey.nD / 2.
        self.i_target = None

        if self.invProb.phi_d < self.target_misfit:
            i_target = 0
            while self.phi_d[i_target] > self.target_misfit:
                i_target += 1
            self.i_target = i_target

    def plot_misfit_curves(self, fname=None, plot_small_smooth=False):

        self.target_misfit = self.invProb.dmisfit.prob.survey.nD / 2.
        self.i_target = None

        if self.invProb.phi_d < self.target_misfit:
            i_target = 0
            while self.phi_d[i_target] > self.target_misfit:
                i_target += 1
            self.i_target = i_target

        fig = plt.figure(figsize=(5, 2))
        ax = plt.subplot(111)
        ax_1 = ax.twinx()
        ax.semilogy(np.arange(len(self.phi_d)), self.phi_d, 'k-', lw=2)
        ax_1.semilogy(np.arange(len(self.phi_d)), self.phi_m, 'r', lw=2)
        if plot_small_smooth:
            ax_1.semilogy(np.arange(len(self.phi_d)), self.phi_m_small, 'ro')
            ax_1.semilogy(np.arange(len(self.phi_d)), self.phi_m_smooth, 'rx')
            ax_1.legend(
                ("$\phi_m$", "small", "smooth"), bbox_to_anchor=(1.5, 1.)
            )

        ax.plot(np.r_[ax.get_xlim()[0], ax.get_xlim()[1]],
                np.ones(2) * self.target_misfit, 'k:')
        ax.set_xlabel("Iteration")
        ax.set_ylabel("$\phi_d$")
        ax_1.set_ylabel("$\phi_m$", color='r')
        for tl in ax_1.get_yticklabels():
            tl.set_color('r')
        plt.show()

    def plot_tikhonov_curves(self, fname=None, dpi=200):

        self.target_misfit = self.invProb.dmisfit.prob.survey.nD / 2.
        self.i_target = None

        if self.invProb.phi_d < self.target_misfit:
            i_target = 0
            while self.phi_d[i_target] > self.target_misfit:
                i_target += 1
            self.i_target = i_target

        fig = plt.figure(figsize=(5, 8))
        ax1 = plt.subplot(311)
        ax2 = plt.subplot(312)
        ax3 = plt.subplot(313)

        ax1.plot(self.beta, self.phi_d, 'k-', lw=2, ms=4)
        ax1.set_xlim(np.hstack(self.beta).min(), np.hstack(self.beta).max())
        ax1.set_xlabel("$\\beta$", fontsize=14)
        ax1.set_ylabel("$\phi_d$", fontsize=14)

        ax2.plot(self.beta, self.phi_m, 'k-', lw=2)
        ax2.set_xlim(np.hstack(self.beta).min(), np.hstack(self.beta).max())
        ax2.set_xlabel("$\\beta$", fontsize=14)
        ax2.set_ylabel("$\phi_m$", fontsize=14)

        ax3.plot(self.phi_m, self.phi_d, 'k-', lw=2)
        ax3.set_xlim(np.hstack(self.phi_m).min(), np.hstack(self.phi_m).max())
        ax3.set_xlabel("$\phi_m$", fontsize=14)
        ax3.set_ylabel("$\phi_d$", fontsize=14)

        if self.i_target is not None:
            ax1.plot(self.beta[self.i_target], self.phi_d[
                     self.i_target], 'k*', ms=10)
            ax2.plot(self.beta[self.i_target], self.phi_m[
                     self.i_target], 'k*', ms=10)
            ax3.plot(self.phi_m[self.i_target], self.phi_d[
                     self.i_target], 'k*', ms=10)

        for ax in [ax1, ax2, ax3]:
            ax.set_xscale("log")
            ax.set_yscale("log")
        plt.tight_layout()
        plt.show()
        if fname is not None:
            fig.savefig(fname, dpi=dpi)


class SaveOutputDictEveryIteration(SaveEveryIteration):
    """
        Saves inversion parameters at every iteraion.
    """

    def initialize(self):
        print(
            "SimPEG.SaveOutputDictEveryIteration will save your inversion progress as dictionary: '###-{0!s}.npz'".format(self.fileName))

    def endIter(self):

        # Initialize the output dict
        outDict = {}
        # Save the data.
        outDict['iter'] = self.opt.iter
        outDict['beta'] = self.invProb.beta
        outDict['phi_d'] = self.invProb.phi_d
        outDict['phi_m'] = self.invProb.phi_m

        phi_s, phi_x, phi_y, phi_z = 0, 0, 0, 0
        for reg in self.reg.objfcts:
            phi_s += (
                reg.objfcts[0](self.invProb.model) * reg.alpha_s
            )
            phi_x += (
                reg.objfcts[1](self.invProb.model) * reg.alpha_x
            )
            if reg.regmesh.dim > 1:
                phi_y += (
                    reg.objfcts[2](self.invProb.model) * reg.alpha_y
                )

            if reg.regmesh.dim > 2:
                phi_z += (
                    reg.objfcts[3](self.invProb.model) * reg.alpha_z
                )

        outDict['phi_ms'] = phi_s
        outDict['phi_mx'] = phi_x
        outDict['phi_my'] = phi_y
        outDict['phi_mz'] = phi_z
        outDict['f'] = self.opt.f
        outDict['m'] = self.invProb.model
        outDict['dpred'] = self.invProb.dpred

        # Save the file as a npz
        np.savez('{:03d}-{:s}'.format(self.opt.iter, self.fileName), outDict)


class Update_IRLS(InversionDirective):

    gamma = None
    phi_d_last = None
    f_old = None
    f_min_change = 1e-2
    beta_tol = 5e-2
    prctile = 95
    chifact_start = 1.
    chifact_target = 1.

    # Solving parameter for IRLS (mode:2)
    IRLSiter = 0
    minGNiter = 5
    maxIRLSiter = 10
    iterStart = 0

    # Beta schedule
    updateBeta = True
    coolingFactor = 2.
    coolingRate = 1
    ComboRegFun = False
    ComboMisfitFun = False
    scale_m = False
    updateBeta = True
    chifact_start = 1
    mode = 1

    @property
    def target(self):
        if getattr(self, '_target', None) is None:
            nD = 0
            for survey in self.survey:
                nD += survey.nD

            self._target = nD * 0.5 * self.chifact_target

        return self._target

    @target.setter
    def target(self, val):
        self._target = val

    @property
    def start(self):
        if getattr(self, '_start', None) is None:
            if isinstance(self.survey, list):
                self._start = 0
                for survey in self.survey:
                    self._start += survey.nD * 0.5 * self.chifact_start

            else:

                self._start = self.survey.nD * 0.5 * self.chifact_start
        return self._start

    @start.setter
    def start(self, val):
        self._start = val

    def initialize(self):

        # Store norms to be applied after the l2 inversion
        self.norms = []
        for reg in self.reg.objfcts:
            self.norms.append(reg.norms)
            reg.norms = [2., 2., 2., 2.]
            reg.model = self.invProb.model

    def endIter(self):

                # Adjust scales for MVI-S
        # if self.ComboMisfitFun:
        if self.scale_m:
            self.regScale()
        # Update the model used by the regularization
        phi_m_last = []
        for reg in self.reg.objfcts:
            reg.model = self.invProb.model
            phi_m_last += [reg(self.invProb.model)]

        # After reaching target misfit with l2-norm, switch to IRLS (mode:2)
        if np.all([self.invProb.phi_d < self.start,
                   self.mode == 1]):
            print(
                "Reached starting chifact with l2-norm regularization: Start IRLS steps...")

            self.mode = 2
            self.iterStart = self.opt.iter
            self.phi_d_last = self.invProb.phi_d
            self.invProb.phi_m_last = self.reg(self.invProb.model)

            # Either use the supplied epsilon, or fix base on distribution of
            # model values

            for reg in self.reg.objfcts:

                if getattr(reg, 'eps_p', None) is None:

                    mtemp = reg.mapping * self.invProb.model
                    reg.eps_p = np.percentile(np.abs(mtemp), self.prctile)
                    print("eps_p caluclate" + str(reg.eps_p))

                if getattr(reg, 'eps_q', None) is None:
                    mtemp = reg.mapping * self.invProb.model
                    reg.eps_q = np.percentile(
                        np.abs(reg.regmesh.cellDiffxStencil * mtemp), self.prctile)

            # Re-assign the norms supplied by user l2 -> lp
            for reg, norms in zip(self.reg.objfcts, self.norms):
                reg.norms = norms
                print("L[p qx qy qz]-norm : " + str(reg.norms))

            # Save l2-model
            self.invProb.l2model = self.invProb.model.copy()

            # Print to screen
            for reg in self.reg.objfcts:
                print("eps_p: " + str(reg.eps_p) +
                      " eps_q: " + str(reg.eps_q))

        # Only update after GN iterations
        if np.all([(self.opt.iter - self.iterStart) % self.minGNiter == 0, self.mode != 1]):

            # Check for maximum number of IRLS cycles
            if self.IRLSiter == self.maxIRLSiter:
                print("Reach maximum number of IRLS cycles: {0:d}".format(
                    self.maxIRLSiter))
                self.opt.stopNextIteration = True
                return

            # phi_m_last = []
            for reg in self.reg.objfcts:

                # # Reset gamma scale
                # phi_m_last += [reg(self.invProb.model)]

                for comp in reg.objfcts:
                    comp.gamma = 1.

            # Remember the value of the norm from previous R matrices
            self.f_old = self.reg(self.invProb.model)

            self.IRLSiter += 1

            # Reset the regularization matrices so that it is
            # recalculated for current model. Do it to all levels of comboObj
            for reg in self.reg.objfcts:

                # If comboObj, go down one more level
                for comp in reg.objfcts:
                    comp.stashedR = None

            # Compute new model objective function value
            phim_new = self.reg(self.invProb.model)

            phi_m_new = []
            for reg in self.reg.objfcts:
                phi_m_new += [reg(self.invProb.model)]

            # phim_new = self.reg(self.invProb.model)
            self.f_change = np.abs(self.f_old - phim_new) / self.f_old

            print("Phim relative change: {0:6.3e}".format((self.f_change)))
            # Check if the function has changed enough
            if self.f_change < self.f_min_change and self.IRLSiter > 1:
                print("Minimum decrease in regularization. End of IRLS")
                self.opt.stopNextIteration = True
                return
            else:
                self.f_old = phim_new

            # Update gamma to scale the regularization between IRLS iterations

            for reg, phim_old, phim_now in zip(self.reg.objfcts, phi_m_last, phi_m_new):

                gamma = phim_old / phim_now

                # If comboObj, go down one more level
                for comp in reg.objfcts:
                    comp.gamma = gamma

            self.updateBeta = True
        # Beta Schedule
        if np.all([self.invProb.phi_d < self.target,
                   self.mode == 2]):
            print("Target chifact overshooted, adjusting beta ...")
            self.mode = 3

        if np.all([self.opt.iter > 0, self.opt.iter % self.coolingRate == 0,
                   self.mode != 3]):

            if self.debug:
                print('BetaSchedule is cooling Beta. Iteration: {0:d}'.format(
                    self.opt.iter))
            self.invProb.beta /= self.coolingFactor

        # Check if misfit is within the tolerance, otherwise scale beta
        if np.all([np.abs(1. - self.invProb.phi_d / self.target) > self.beta_tol,
                   self.updateBeta,
                   self.mode == 3]):

            self.invProb.beta = (self.invProb.beta * self.target /
                                 self.invProb.phi_d)
            self.updateBeta = False

    def regScale(self):
        """
            Update the scales used by regularization
        """

        # Currently implemented specifically for MVI-S
        # Need to be generalized if used by others
        # Currently implemented for MVI-S only
        max_p = []
        for reg in self.reg.objfcts[0].objfcts:
            eps_p = reg.epsilon
            norm_p = 2  # self.reg.objfcts[0].norms[0]
            f_m = abs(reg.f_m)
            max_p += [np.max(eps_p**(1 - norm_p / 2.) * f_m /
                             (f_m**2. + eps_p**2.)**(1 - norm_p / 2.))]

        max_p = np.asarray(max_p)

        max_s = [np.pi, np.pi]
        for obj, var in zip(self.reg.objfcts[1:], max_s):
            obj.scale = max_p.max() / var

    def validate(self, directiveList):
        # check if a linear preconditioner is in the list, if not warn else
        # assert that it is listed after the IRLS directive
        dList = directiveList.dList
        self_ind = dList.index(self)
        lin_precond_ind = [
            isinstance(d, UpdatePreconditioner) for d in dList
        ]

        if any(lin_precond_ind):
            assert(lin_precond_ind.index(True) > self_ind), (
                "The directive 'UpdatePreconditioner' must be after Update_IRLS "
                "in the directiveList"
            )
        else:
            warnings.warn(
                "Without a Linear preconditioner, convergence may be slow. "
                "Consider adding `Directives.UpdatePreconditioner` to your "
                "directives list"
            )
        return True


class UpdatePreconditioner(InversionDirective):
    """
    Create a Jacobi preconditioner for the linear problem
    """
    onlyOnStart = False
    mapping = None
    ComboObjFun = False
    misfitDiag = None

    def initialize(self):

        if getattr(self.opt, 'approxHinv', None) is None:

            m = self.invProb.model

            if getattr(self.opt, 'JtJdiag', None) is None:

                JtJdiag = np.zeros_like(self.invProb.model)
                for prob, dmisfit in zip(self.prob, self.dmisfit.objfcts):

                    assert getattr(prob, 'getJ', None) is not None, (
                        "Problem does not have a getJ attribute." +
                        "Cannot form the sensitivity explicitely"
                    )

                    JtJdiag += np.sum((dmisfit.W * prob.getJ(m))**2., axis=0)

                self.opt.JtJdiag = JtJdiag

            # Update the pre-conditioner
            reg_diag = np.zeros_like(self.invProb.model)
            for reg in self.reg.objfcts:
                W = reg.deriv2(m)
                reg_diag += self.invProb.beta * W.diagonal()
            Hdiag = self.opt.JtJdiag + reg_diag

            PC = Utils.sdiag(Hdiag**-1.)
            self.opt.approxHinv = PC

    def endIter(self):
        # Cool the threshold parameter
        if self.onlyOnStart is True:
            return

        if getattr(self.opt, 'approxHinv', None) is not None:
            m = self.invProb.model
            # Update the pre-conditioner
            reg_diag = np.zeros_like(self.invProb.model)
            for reg in self.reg.objfcts:
                W = reg.deriv2(m)
                reg_diag += self.invProb.beta * W.diagonal()

            Hdiag = self.opt.JtJdiag + reg_diag

            PC = Utils.sdiag(Hdiag**-1.)
            self.opt.approxHinv = PC


class Update_Wj(InversionDirective):
    """
        Create approx-sensitivity base weighting using the probing method
    """
    k = None  # Number of probing cycles
    itr = None  # Iteration number to update Wj, or always update if None

    def endIter(self):

        if self.itr is None or self.itr == self.opt.iter:

            m = self.invProb.model
            if self.k is None:
                self.k = int(self.survey.nD / 10)

            def JtJv(v):

                Jv = self.prob.Jvec(m, v)

                return self.prob.Jtvec(m, Jv)

            JtJdiag = Utils.diagEst(JtJv, len(m), k=self.k)
            JtJdiag = JtJdiag / max(JtJdiag)

            self.reg.wght = JtJdiag


###############################################################################
#                                                                             #
#         Directives for Petrophysically-Constrained Regularization           #
#                                                                             #
###############################################################################

class GaussianMixtureUpdateModel(InversionDirective):

    coolingFactor = 1.
    coolingRate = 1
    update_covariances = False
    verbose = False
    alphadir = None
    nu = None
    kappa = None
    fixed_membership = None
    keep_ref_fixed_in_Smooth = True

    def endIter(self):
        m = self.invProb.model
        modellist = self.invProb.reg.wiresmap * m
        model = np.c_[
            [a * b for a, b in zip(self.invProb.reg.maplist, modellist)]].T

        if (self.alphadir is None) or (self.nu is None) or (self.kappa is None):
            self.alphadir = (self.invProb.reg.gamma) * \
                np.ones(self.invProb.reg.GMmref.n_components)
            self.nu = self.invProb.reg.gamma * \
                np.ones(self.invProb.reg.GMmref.n_components)
            self.kappa = self.invProb.reg.gamma * \
                np.ones(self.invProb.reg.GMmref.n_components)

        if self.invProb.reg.mrefInSmooth:
            self.fixed_membership = self.invProb.reg.membership(self.invProb.reg.mref)

        clfupdate = Utils.Petro_Utils.GaussianMixtureWithPrior(
            GMref=self.invProb.reg.GMmref,
            alphadir=self.alphadir,
            kappa=self.kappa,
            nu=self.nu,
            verbose=self.verbose,
            prior_type='semi',
            update_covariances=self.update_covariances,
            max_iter=self.invProb.reg.GMmodel.max_iter,
            n_init=self.invProb.reg.GMmodel.n_init,
            reg_covar=self.invProb.reg.GMmodel.reg_covar,
            weights_init=self.invProb.reg.GMmodel.weights_,
            means_init=self.invProb.reg.GMmodel.means_,
            precisions_init=self.invProb.reg.GMmodel.precisions_,
            random_state=self.invProb.reg.GMmodel.random_state,
            tol=self.invProb.reg.GMmodel.tol,
            verbose_interval=self.invProb.reg.GMmodel.verbose_interval,
            warm_start=self.invProb.reg.GMmodel.warm_start,
            fixed_membership=self.fixed_membership,
        )
        clfupdate = clfupdate.fit(model)

        self.invProb.reg.GMmodel = clfupdate
        if self.fixed_membership is None:
            membership = clfupdate.predict(model)
            self.invProb.reg.mref = Utils.mkvc(clfupdate.means_[membership])
            self.invProb.reg.objfcts[0]._r_second_deriv = None
        else:
            self.invProb.reg.mref = Utils.mkvc(
                clfupdate.means_[self.fixed_membership])

        if self.opt.iter > 0 and self.opt.iter % self.coolingRate == 0:
            if self.debug:
                print(
                    'BetaSchedule is cooling Beta. Iteration: {0:d}'
                    .format(self.opt.iter)
                )
            self.invProb.reg.gamma /= self.coolingFactor


class UpdateReference(InversionDirective):

    def endIter(self):
        m = self.invProb.model
        modellist = self.invProb.reg.wiresmap * m
        model = np.c_[
            [a * b for a, b in zip(self.invProb.reg.maplist, modellist)]].T

        membership = self.invProb.reg.GMmref.predict(model)
        self.invProb.reg.mref = Utils.mkvc(self.invProb.reg.GMmref.means_[membership])
        self.invProb.reg.objfcts[0]._r_second_deriv = None

# class GaussianMixtureUpdateModel(InversionDirective):

#     coolingFactor = 1.
#     coolingRate = 1
#     update_covariances = False
#     verbose = False
#     alphadir = None
#     nu = None
#     kappa = None

#     def endIter(self):
#         m = self.invProb.model
#         modellist = self.invProb.reg.wiresmap * m
#         model = np.c_[
#             [a * b for a, b in zip(self.invProb.reg.maplist, modellist)]].T

#         clfupdate = Utils.GaussianMixture(
#             n_components=self.invProb.reg.GMmref.n_components,
#             covariance_type=self.invProb.reg.GMmref.covariance_type,
#             max_iter=self.invProb.reg.GMmref.max_iter,
#             n_init=self.invProb.reg.GMmref.n_init,
#             reg_covar=self.invProb.reg.GMmref.reg_covar,
#             # weights_init=self.invProb.reg.GMmref.weights_,
#             # means_init=self.invProb.reg.GMmref.means_,
#             # precisions_init=self.invProb.reg.GMmref.precisions_
#         )
#         clfupdate = clfupdate.fit(model)

#         if (self.alphadir is None) or (self.nu is None) or (self.kappa is None):
#             self.alphadir = self.invProb.reg.gamma
#             self.nu = self.invProb.reg.gamma
#             self.kappa = self.invProb.reg.gamma

#         # if self.verbose:
#         #     print('before order means: ', clfupdate.means_)
#         #     print('before order weights: ', clfupdate.weights_)
#         #     print('before order precisions: ', clfupdate.precisions_)
#         Utils.order_cluster(clfupdate, self.invProb.reg.GMmref)
#         if self.verbose:
#             print('before update means: ', clfupdate.means_)
#             print('before update weights: ', clfupdate.weights_)
#             print('before update precisions: ', clfupdate.precisions_)

#         for k in range(clfupdate.n_components):
#             clfupdate.means_[k] = (1. / (1. + self.nu[k])) * (
# clfupdate.means_[k] + self.nu[k] * self.invProb.reg.GMmref.means_[k])

#             if self.invProb.reg.GMmref.covariance_type == 'tied':
#                 pass
#             elif self.update_covariances:
#                 clfupdate.covariances_[k] = (1. / (1. + self.kappa[k])) * (clfupdate.covariances_[
#                     k] + self.kappa[k] * self.invProb.reg.GMmref.covariances_[k])
#             else:
#                 clfupdate.precisions_[k] = (1. / (1. + self.kappa[k])) * (clfupdate.precisions_[
# k] + self.kappa[k] * self.invProb.reg.GMmref.precisions_[k])

#             clfupdate.weights_[k] = (1. / (1. + self.alphadir[k])) * (
# clfupdate.weights_[k] + self.alphadir[k] *
# self.invProb.reg.GMmref.weights_[k])

#         if self.invProb.reg.GMmref.covariance_type == 'tied':
#             if self.update_covariances:
#                 clfupdate.covariances_ = (1. / (1. + self.kappa[0])) * (clfupdate.covariances_ + self.kappa[0] * self.invProb.reg.GMmref.covariances_)
#                 clfupdate.precisions_cholesky_ = Utils._compute_precision_cholesky(
#                     clfupdate.covariances_, clfupdate.covariance_type)
#                 Utils.computePrecision(clfupdate)
#             else:
#                 clfupdate.precisions_ = (1. / (1. + self.kappa[0])) * (clfupdate.precisions_ + self.kappa[0] * self.invProb.reg.GMmref.precisions_)
#                 clfupdate.covariances_cholesky_ = Utils._compute_precision_cholesky(
#                     clfupdate.precisions_, clfupdate.covariance_type)
#                 Utils.computeCovariance(clfupdate)
#                 clfupdate.precisions_cholesky_ = Utils._compute_precision_cholesky(
#                     clfupdate.covariances_, clfupdate.covariance_type)
#         elif self.update_covariances:
#             clfupdate.precisions_cholesky_ = Utils._compute_precision_cholesky(
#                 clfupdate.covariances_, clfupdate.covariance_type)
#             Utils.computePrecision(clfupdate)
#         else:
#             clfupdate.covariances_cholesky_ = Utils._compute_precision_cholesky(
#                 clfupdate.precisions_, clfupdate.covariance_type)
#             Utils.computeCovariance(clfupdate)
#             clfupdate.precisions_cholesky_ = Utils._compute_precision_cholesky(
#                 clfupdate.covariances_, clfupdate.covariance_type)

#         membership = clfupdate.predict(model)
#         self.invProb.reg.GMmodel = clfupdate
#         self.invProb.reg.mref = Utils.mkvc(clfupdate.means_[membership])

#         if self.verbose:
#             print('after update means: ', clfupdate.means_)
#             print('after update weights: ', clfupdate.weights_)
#             print('after update precisions: ', clfupdate.precisions_)

#         if self.opt.iter > 0 and self.opt.iter % self.coolingRate == 0:
#             if self.debug:
#                 print(
#                     'BetaSchedule is cooling Beta. Iteration: {0:d}'
#                     .format(self.opt.iter)
#                 )
#             self.invProb.reg.gamma /= self.coolingFactor


class FuzzyGaussianMixtureWithPriorUpdateModel(InversionDirective):

    coolingFactor = 1.
    coolingRate = 1
    update_covariances = False
    verbose = False
    max_iter = 100
    alphadir = 1.
    nu = 0.
    kappa = 0.
    fuzzyness = 2.

    def endIter(self):

        m = self.invProb.model
        modellist = self.invProb.reg.wiresmap * m
        model = np.c_[
            [a * b for a, b in zip(self.invProb.reg.maplist, modellist)]].T

        clfupdate = Utils.FuzzyGaussianMixtureWithPrior(
            GMref=self.invProb.reg.GMmref,
            kappa=self.kappa,
            nu=self.nu,
            alphadir=self.alphadir,
            fuzzyness=self.fuzzyness,
            reg_covar=self.invProb.reg.GMmref.reg_covar,
            GMinit=self.invProb.reg.GMmref,
            init_params='kmeans', max_iter=self.max_iter,
            # means_init=None, n_components=3, n_init=10, precisions_init=None,
            # random_state=None, reg_covar=1e-06, tol=0.001, verbose=0,
            # verbose_interval=10, warm_start=False, weights_init=None,
        )

        clfupdate.FitFuzzyWithConjugatePrior(model)
        Utils.order_cluster(clfupdate, self.invProb.reg.GMmref)

        membership = clfupdate.predict(model)
        self.invProb.reg.GMmodel = clfupdate
        self.invProb.reg.mref = Utils.mkvc(clfupdate.means_[membership])

        if self.opt.iter > 0 and self.opt.iter % self.coolingRate == 0:
            if self.debug:
                print(
                    'BetaSchedule is cooling Beta. Iteration: {0:d}'
                    .format(self.opt.iter)
                )
            self.alphadir /= self.coolingFactor
            self.kappa /= self.coolingFactor
            self.nu /= self.coolingFactor


class SmoothUpdateReferenceModel(InversionDirective):

    neighbors = None
    distance = 2
    wegithed_random_walk = True
    compute_score = False
    maxit = None
    verbose = False
    method = 'ICM'  # 'Gibbs'
    offdiag = 0.
    indiag = 1.
    Pottmatrix = None
    log_univar = None

    def endIter(self):
        mesh = self.invProb.reg._mesh
        if self.neighbors is None:
            self.neighbors = 2 * mesh.dim

        m = self.invProb.model
        modellist = self.invProb.reg.wiresmap * m
        model = np.c_[
            [a * b for a, b in zip(self.invProb.reg.maplist, modellist)]].T
        minit = self.invProb.reg.GMmodel.predict(model)

        indActive = self.invProb.reg.indActive

        if self.Pottmatrix is None:
            n_unit = self.invProb.reg.GMmodel.n_components
            Pott = np.ones([n_unit, n_unit]) * self.offdiag
            for i in range(Pott.shape[0]):
                Pott[i, i] = self.indiag
            self.Pottmatrix = Pott

        # if self.log_univar is None:
        self.log_univar = np.log(
            self.invProb.reg.GMmodel.predict_proba(model))

        if self.method == 'Gibbs':
            denoised = Utils.GibbsSampling_PottsDenoising(mesh, minit,
                                                          self.log_univar,
                                                          self.Pottmatrix,
                                                          indActive=indActive,
                                                          neighbors=self.neighbors,
                                                          norm=self.distance,
                                                          weighted_selection=self.wegithed_random_walk,
                                                          compute_score=self.compute_score,
                                                          maxit=self.maxit,
                                                          verbose=self.verbose)
        elif self.method == 'ICM':
            denoised = Utils.ICM_PottsDenoising(mesh, minit,
                                                self.log_univar,
                                                self.Pottmatrix,
                                                indActive=indActive,
                                                neighbors=self.neighbors,
                                                norm=self.distance,
                                                weighted_selection=self.wegithed_random_walk,
                                                compute_score=self.compute_score,
                                                maxit=self.maxit,
                                                verbose=self.verbose)

        self.invProb.reg.mref = Utils.mkvc(
            self.invProb.reg.GMmodel.means_[denoised[0]])


class BoreholeLithologyConstraints(InversionDirective):

    borehole_index = None
    borehole_lithology = None

    def endIter(self):
        membership = self.invProb.reg.membership(self.invProb.reg.mref)

        membership[self.borehole_index] = self.borehole_lithology

        self.invProb.reg.mref = Utils.mkvc(
            self.invProb.reg.GMmodel.means_[membership]
        )


class AlphasSmoothEstimate_ByEig(InversionDirective):
    """AlhaEstimate"""

    alpha0 = 1.       #: The initial Alha (regularization parameter)
    alpha0_ratio = 1e-2  #: estimateAlha0 is used with this ratio
    ninit = 10
    verbose = False

    def initialize(self):
        """
        """
        nbr = len(self.invProb.reg.objfcts)
        if isinstance(self.invProb.reg, ObjectiveFunction.ComboObjectiveFunction):
            print('careful, alphas may not have been chosen properly')

        Smooth = np.r_[[(
            isinstance(regpart, Regularization.SmoothDeriv) or
            isinstance(regpart, Regularization.SimpleSmoothDeriv))
            for regpart in self.invProb.reg.objfcts]]

        if not isinstance(self.alpha0_ratio, np.ndarray):
            self.alpha0_ratio = self.alpha0_ratio * np.ones(nbr)

        if not isinstance(self.alpha0, np.ndarray):
            self.alpha0 = self.alpha0 * np.ones(nbr)

        if self.debug:
            print('Calculating the Alpha0 parameter.')

        m = self.invProb.model

        for i in range(nbr):
            ratio = []
            if Smooth[i]:
                for j in range(self.ninit):
                    x0 = np.random.rand(m.shape[0])
                    t = x0.dot(self.invProb.reg.objfcts[0].deriv2(m, v=x0))
                    b = x0.dot(self.invProb.reg.objfcts[i].deriv2(m, v=x0))
                    ratio.append(t / b)

                self.alpha0[i] *= self.alpha0_ratio[i] * np.median(ratio)
                mtype = self.invProb.reg.objfcts[i]._multiplier_pair
                setattr(self.invProb.reg, mtype, self.alpha0[i])

        if self.verbose:
            print('Alpha scales: ', self.invProb.reg.multipliers)


class PetroTargetMisfit(InversionDirective):

    verbose = False
    # Chi factor for Data Misfit
    chifact = 1.
    phi_d_star = None

    # Chifact for Clustering/Smallness
    TriggerSmall = True
    chiSmall = 1.
    phi_ms_star = None

    # Tolerance for Distribution parameters
    TriggerTheta = False
    ToleranceTheta = 1.
    distance_norm = np.inf

    AllStop = False
    DM = False
    CL = False
    DP = False

    def initialize(self):
        self.dmlist = np.r_[[dmis(self.invProb.model)
                             for dmis in self.dmisfit.objfcts]]

    @property
    def DMtarget(self):
        if getattr(self, '_DMtarget', None) is None:
            # the factor of 0.5 is because we do phid = 0.5*|| dpred - dobs||^2
            if self.phi_d_star is None:
                # Check if it is a ComboObjective
                if isinstance(self.dmisfit, ObjectiveFunction.ComboObjectiveFunction):
                    self.phi_d_star = np.r_[
                        [0.5 * survey.nD for survey in self.survey]]
                else:
                    self.phi_d_star = np.r_[
                        [0.5 * self.invProb.dmisfit.survey.nD]]

            self._DMtarget = self.chifact * self.phi_d_star
        return self._DMtarget

    @DMtarget.setter
    def DMtarget(self, val):
        self._DMtarget = val

    @property
    def CLtarget(self):
        if getattr(self, '_CLtarget', None) is None:
            # the factor of 0.5 is because we do phid = 0.5*|| dpred - dobs||^2
            if self.phi_ms_star is None:
                # Expected value is number of active cells * number of physical
                # properties
                self.phi_ms_star = 0.5 * len(self.invProb.model)

            self._CLtarget = self.chiSmall * self.phi_ms_star
        return self._CLtarget

    @CLtarget.setter
    def CLtarget(self, val):
        self._CLtarget = val

    def phims(self):
        return self.invProb.reg.objfcts[0](self.invProb.model, externalW=False)

    def ThetaTarget(self):
        maxdiff = 0.

        for i in range(self.invProb.reg.GMmodel.n_components):
            meandiff = np.linalg.norm((self.invProb.reg.GMmodel.means_[i] - self.invProb.reg.GMmref.means_[i]) / self.invProb.reg.GMmref.means_[i],
                                      ord=self.distance_norm)
            maxdiff = np.maximum(maxdiff, meandiff)

            if self.invProb.reg.GMmodel.covariance_type == 'full' or self.invProb.reg.GMmodel.covariance_type == 'spherical':
                covdiff = np.linalg.norm((self.invProb.reg.GMmodel.covariances_[i] - self.invProb.reg.GMmref.covariances_[i]) / self.invProb.reg.GMmref.covariances_[i],
                                         ord=self.distance_norm)
            else:
                covdiff = np.linalg.norm((self.invProb.reg.GMmodel.covariances_ - self.invProb.reg.GMmref.covariances_) / self.invProb.reg.GMmref.covariances_,
                                         ord=self.distance_norm)
            maxdiff = np.maximum(maxdiff, covdiff)

            pidiff = np.linalg.norm([(self.invProb.reg.GMmodel.weights_[i] - self.invProb.reg.GMmref.weights_[i]) / self.invProb.reg.GMmref.weights_[i]],
                                    ord=self.distance_norm)
            maxdiff = np.maximum(maxdiff, pidiff)

        return maxdiff

    def endIter(self):

        self.AllStop = False
        self.DM = False
        self.CL = True
        self.DP = True
        self.dmlist = np.r_[[dmis(self.invProb.model)
                             for dmis in self.dmisfit.objfcts]]
        self.targetlist = np.r_[
            [dm < tgt for dm, tgt in zip(self.dmlist, self.DMtarget)]]

        if np.all(self.targetlist):
            self.DM = True

        if (self.TriggerSmall):
            if (self.phims() > self.CLtarget):
                self.CL = False

        if (self.TriggerTheta):
            if (self.ThetaTarget() > self.ToleranceTheta):
                self.DP = False

        self.AllStop = self.DM and self.CL and self.DP
        if self.verbose:
            print(
                'DM: ', self.dmlist, self.targetlist,
                '; CL: ', self.phims(), self.CL,
                '; DP: ', self.DP,
                '; All:', self.AllStop
            )
        if self.AllStop:
            self.opt.stopNextIteration = True


class ScalingEstimate_ByEig(InversionDirective):
    """BetaEstimate"""

    Chi0 = None       #: The initial Beta (regularization parameter)
    Chi0_ratio = 1  #: estimateBeta0 is used with this ratio
    ninit = 1
    verbose = False

    def initialize(self):
        """
           Assume only 2 data misfits
        """

        if self.debug:
            print('Calculating the scaling parameter.')

        if len(self.dmisfit.objfcts) == 1:
            raise Exception('This Directives only applies ot joint inversion')

        # if not isinstance(self.Chi0_ratio,np.ndarray):
        #    self.Chi0_ratio = self.Chi0_ratio*np.ones(2)

        m = self.invProb.model
        f = self.invProb.getFields(m, store=True, deleteWarmstart=False)

        ratio = []
        for i in range(self.ninit):
            x0 = np.random.rand(*m.shape)
            t, b = 0, 0
            t = x0.dot(self.dmisfit.objfcts[0].deriv2(m, x0, f=f[0]))
            b = x0.dot(self.dmisfit.objfcts[1].deriv2(m, x0, f=f[1]))
            ratio.append(t / b)

        self.ratio = ratio
        self.Chi0 = self.Chi0_ratio * np.median(ratio)
        self.dmisfit.multipliers[0] = 1.
        self.dmisfit.multipliers[1] = self.Chi0
        self.dmisfit.multipliers /= np.sum(self.dmisfit.multipliers)

        if self.verbose:
            print('Scale Multipliers: ', self.dmisfit.multipliers)


class JointScalingSchedule(InversionDirective):

    verbose = False
    tolerance = 0.02
    progress = 0.02
    rateCooling = 1.
    rateWarming = 1.
    mode = 1
    chimax = 1e10
    chimin = 1e-10
    UpdateRate = 3

    def initialize(self):

        targetclass = np.r_[[isinstance(
            dirpart, PetroTargetMisfit) for dirpart in self.inversion.directiveList.dList]]
        if ~np.any(targetclass):
            self.DMtarget = None
        else:
            self.targetclass = np.where(targetclass)[0][-1]
            self.DMtarget = self.inversion.directiveList.dList[
                self.targetclass].DMtarget

    def endIter(self):

        self.dmlist = self.inversion.directiveList.dList[
            self.targetclass].dmlist

        if np.any(self.dmlist < self.DMtarget):
            self.mode = 2
        else:
            self.mode = 1

        if self.opt.iter > 0 and self.opt.iter % self.UpdateRate == 0:

            if self.mode == 2:

                if np.all(np.r_[self.dmisfit.multipliers] > self.chimin) and np.all(np.r_[self.dmisfit.multipliers] < self.chimax):

                    # Assume only 2 data misfit
                    indx = self.dmlist > self.DMtarget
                    if np.any(indx):
                        self.dmisfit.multipliers[np.where(
                            indx)[0][0]] *= self.rateWarming * (self.DMtarget[~indx] / self.dmlist[~indx])[0]
                        self.dmisfit.multipliers = self.dmisfit.multipliers / \
                            np.sum(self.dmisfit.multipliers)

                        if self.verbose:
                            print('update scaling for data misfit')
                            print('new scale:', self.dmisfit.multipliers)


class PetroBetaReWeighting(InversionDirective):

    verbose = False
    tolerance = 0.02
    progress = 0.02
    rateCooling = 2.
    rateWarming = 1.
    mode = 1
    mode2_iter = 0
    betamax = 1e10
    betamin = 1e-10
    UpdateRate = 3
    ratio_in_cooling = True

    update_prior_confidence = False
    progress_gamma_warming = 0.02
    progress_gamma_cooling = 0.02
    gamma_max = 1e10
    gamma_min = 1e-1
    ratio_in_gamma_cooling = True
    ratio_in_gamma_warming = True
    alphadir_rateCooling = 1.
    kappa_rateCooling = 1.
    nu_rateCooling = 1.
    alphadir_rateWarming = 1.
    kappa_rateWarming = 1.
    nu_rateWarming = 1.

    force_prior_increase = False
    force_prior_increase_rate = 10.

    def initialize(self):

        # self.previous_phid = self.invProb.phi_d
        self.previous_score = self.invProb.reg.objfcts[
            0](self.invProb.model, externalW=False)
        targetclass = np.r_[[isinstance(
            dirpart, PetroTargetMisfit) for dirpart in self.inversion.directiveList.dList]]
        if ~np.any(targetclass):
            self.DMtarget = None
        else:
            self.targetclass = np.where(targetclass)[0][-1]
            self.DMtarget = np.sum(
                np.r_[self.dmisfit.multipliers] *
                self.inversion.directiveList.dList[self.targetclass].DMtarget
            )
            self.previous_dmlist = self.inversion.directiveList.dList[
                self.targetclass].dmlist
            self.CLtarget = self.inversion.directiveList.dList[
                self.targetclass].CLtarget

        updategaussianclass = np.r_[[isinstance(
            dirpart, GaussianMixtureUpdateModel) for dirpart in self.inversion.directiveList.dList]]
        if ~np.any(updategaussianclass):
            self.DMtarget = None
        else:
            updategaussianclass = np.where(updategaussianclass)[0][-1]
            self.updategaussianclass = self.inversion.directiveList.dList[
                updategaussianclass]

    def endIter(self):

        self.DM = self.inversion.directiveList.dList[self.targetclass].DM
        self.dmlist = self.inversion.directiveList.dList[
            self.targetclass].dmlist
        self.DMtarget = self.inversion.directiveList.dList[
            self.targetclass].DMtarget
        self.TotalDMtarget = np.sum(
            np.r_[self.dmisfit.multipliers] * self.inversion.directiveList.dList[self.targetclass].DMtarget)
        self.score = self.inversion.directiveList.dList[
            self.targetclass].phims()

        if self.DM:
            self.mode = 2
            self.mode2_iter += 1
            if self.mode2_iter == 1 and self.force_prior_increase:
                if self.ratio_in_gamma_warming:
                    ratio = self.score / self.CLtarget
                else:
                    ratio = 1.
                self.updategaussianclass.alphadir *= self.force_prior_increase_rate * ratio
                self.updategaussianclass.alphadir = np.minimum(
                    self.gamma_max *
                    np.ones_like(self.updategaussianclass.alphadir),
                    self.updategaussianclass.alphadir
                )
                self.updategaussianclass.kappa *= self.force_prior_increase_rate * ratio
                self.updategaussianclass.kappa = np.minimum(
                    self.gamma_max *
                    np.ones_like(self.updategaussianclass.kappa),
                    self.updategaussianclass.kappa
                )
                self.updategaussianclass.nu *= self.force_prior_increase_rate * ratio
                self.updategaussianclass.nu = np.minimum(
                    self.gamma_max *
                    np.ones_like(self.updategaussianclass.nu),
                    self.updategaussianclass.nu
                )

                if self.verbose:
                    print(
                        'Mode 2 started. Increased GMM Prior. New confidences:\n',
                        'nu: ', self.updategaussianclass.nu,
                        '\nkappa: ', self.updategaussianclass.kappa,
                        '\nalphadir: ', self.updategaussianclass.alphadir
                    )

        # else:
        #    self.mode = 1

        if self.opt.iter > 0 and self.opt.iter % self.UpdateRate == 0:
            if self.verbose:
                print('progress', self.dmlist, '><',
                      (1. - self.progress) * self.previous_dmlist)
            if np.any(
                [
                    np.all(
                        [
                            np.all(
                                self.dmlist > (1. - self.progress) *
                                self.previous_dmlist
                            ),
                            not self.DM,
                            self.mode == 1
                        ]
                    ),
                    # np.all([not self.DM, self.mode == 2]),
                    np.all(
                        [
                            np.all(
                                self.dmlist > (
                                    1. + self.tolerance) * self.DMtarget
                            ),
                            self.mode == 2
                        ]
                    ),
                ]
            ):

                if np.all([self.invProb.beta > self.betamin]):

                    ratio = 1.
                    indx = self.dmlist > (1. + self.tolerance) * self.DMtarget
                    if np.any(indx) and self.ratio_in_cooling:
                        ratio = np.max(
                            [self.dmlist[indx] / self.DMtarget[indx]])
                    self.invProb.beta /= (self.rateCooling * ratio)

                    if self.verbose:
                        print('update beta for countering plateau')

            elif np.all([self.DM,
                         self.mode == 2]):

                if np.all([self.invProb.beta < self.betamax]):

                    ratio = np.min(self.DMtarget / self.dmlist)
                    self.invProb.beta = self.rateWarming * self.invProb.beta * ratio

                    if self.verbose:
                        print('update beta for clustering')

                if np.all([
                    self.update_prior_confidence,
                    self.score > self.CLtarget,
                    self.score > (1. - self.progress_gamma_cooling) *
                    self.previous_score,
                    self.mode2_iter > 1]
                ):
                    if self.ratio_in_gamma_cooling:
                        ratio = self.score / self.CLtarget
                    else:
                        ratio = 1.
                    self.updategaussianclass.alphadir /= self.alphadir_rateCooling * ratio
                    self.updategaussianclass.alphadir = np.maximum(
                        self.gamma_min *
                        np.ones_like(self.updategaussianclass.alphadir),
                        self.updategaussianclass.alphadir
                    )
                    self.updategaussianclass.kappa /= self.kappa_rateCooling * ratio
                    self.updategaussianclass.kappa = np.maximum(
                        self.gamma_min *
                        np.ones_like(self.updategaussianclass.kappa),
                        self.updategaussianclass.kappa
                    )
                    self.updategaussianclass.nu /= self.nu_rateCooling * ratio
                    self.updategaussianclass.nu = np.maximum(
                        self.gamma_min *
                        np.ones_like(self.updategaussianclass.nu),
                        self.updategaussianclass.nu
                    )

                elif np.all([
                    self.update_prior_confidence,
                    self.score > self.CLtarget,
                    self.score < (1. - self.progress_gamma_warming) *
                    self.previous_score,
                    self.mode2_iter > 1]
                ):
                    if self.ratio_in_gamma_warming:
                        ratio = self.score / self.CLtarget
                    else:
                        ratio = 1.
                    self.updategaussianclass.alphadir *= self.alphadir_rateWarming * ratio
                    self.updategaussianclass.alphadir = np.minimum(
                        self.gamma_max *
                        np.ones_like(self.updategaussianclass.alphadir),
                        self.updategaussianclass.alphadir
                    )
                    self.updategaussianclass.kappa *= self.kappa_rateWarming * ratio
                    self.updategaussianclass.kappa = np.minimum(
                        self.gamma_max *
                        np.ones_like(self.updategaussianclass.kappa),
                        self.updategaussianclass.kappa
                    )
                    self.updategaussianclass.nu *= self.nu_rateWarming * ratio
                    self.updategaussianclass.nu = np.minimum(
                        self.gamma_max *
                        np.ones_like(self.updategaussianclass.nu),
                        self.updategaussianclass.nu
                    )

                    if self.verbose:
                        print(
                            'Increased GMM Prior. New confidences:\n',
                            'nu: ', self.updategaussianclass.nu,
                            '\nkappa: ', self.updategaussianclass.kappa,
                            '\nalphadir: ', self.updategaussianclass.alphadir
                        )

        # self.previous_phid = copy.deepcopy(self.invProb.phi_d)
        self.previous_score = copy.deepcopy(self.score)
        self.previous_dmlist = copy.deepcopy(
            self.inversion.directiveList.dList[self.targetclass].dmlist)


class AddMrefInSmooth(InversionDirective):

    # Chi factor for Data Misfit
    chifact = 1.
    phi_d_target = None
    wait_till_stable = False
    tolerance = 0.
    verbose = False

    def initialize(self):
        targetclass = np.r_[[isinstance(
            dirpart, PetroTargetMisfit) for dirpart in self.inversion.directiveList.dList]]
        if ~np.any(targetclass):
            self.DMtarget = None
        else:
            self.targetclass = np.where(targetclass)[0][-1]
            self._DMtarget = self.inversion.directiveList.dList[
                self.targetclass].DMtarget

        self.nbr = len(self.invProb.reg.objfcts)
        if isinstance(self.invProb.reg, ObjectiveFunction.ComboObjectiveFunction):
            print('careful, mref may not have been set properly in Smoothness')

        self.Smooth = np.r_[[(
            isinstance(regpart, Regularization.SmoothDeriv) or
            isinstance(regpart, Regularization.SimpleSmoothDeriv))
            for regpart in self.invProb.reg.objfcts]]

        self.previous_membership = self.invProb.reg.membership(
            self.invProb.reg.mref)

    @property
    def DMtarget(self):
        if getattr(self, '_DMtarget', None) is None:
            self.phi_d_target = 0.5 * self.invProb.dmisfit.survey.nD
            self._DMtarget = self.chifact * self.phi_d_target
        return self._DMtarget

    @DMtarget.setter
    def DMtarget(self, val):
        self._DMtarget = val

    def endIter(self):
        self.DM = self.inversion.directiveList.dList[self.targetclass].DM
        self.membership = self.invProb.reg.membership(self.invProb.reg.mref)

        same_mref = np.all(self.membership == self.previous_membership)
        if self.verbose:
            print(
                'mref changes in ',
                len(self.membership) - np.count_nonzero(
                    self.previous_membership == self.membership
                ),
                ' places'
            )
        if self.DM and (same_mref or not self.wait_till_stable):
            self.invProb.reg.mrefInSmooth = True

            if self.verbose:
                print('add mref to Smoothness')

            for i in range(self.nbr):
                if self.Smooth[i]:
                    self.invProb.reg.objfcts[i].mref = self.invProb.reg.mref

        self.previous_membership = copy.deepcopy(self.membership)

###############################################################################
#                                                                             #
#         Directives for Sensivity Weighting in DC                            #
#                                                                             #
###############################################################################


class UpdateSensitivityWeights(InversionDirective):
    """
    Directive to take care of re-weighting
    the non-linear magnetic problems.

    """

    mapping = None
    JtJdiag = None
    everyIter = True
    threshold = 1e-12

    def initialize(self):

        # Calculate and update sensitivity
        # for optimization and regularization
        self.update()

    def endIter(self):

        if self.everyIter:
            # Update inverse problem
            self.update()

    def update(self):

        # Get sum square of columns of J
        self.getJtJdiag()

        # Compute normalized weights
        self.wr = self.getWr()

        # Send a copy of JtJdiag for the preconditioner
        self.updateOpt()

        # Update the regularization
        self.updateReg()

    def getJtJdiag(self):
        """
            Compute explicitely the main diagonal of JtJ
            Good for any problem where J is formed explicitely
        """
        self.JtJdiag = []

        for prob, survey, dmisfit in zip(self.prob,
                                         self.survey,
                                         self.dmisfit.objfcts):

            assert getattr(prob, 'getJ', None) is not None, (
                "Problem does not have a getJ attribute." +
                "Cannot form the sensitivity explicitely"
            )

            m = self.invProb.model

            self.JtJdiag += [np.sum((dmisfit.W * prob.getJ(m))**2., axis=0)]

        return self.JtJdiag

    def getWr(self):
        """
            Take the diagonal of JtJ and return
            a normalized sensitivty weighting vector
        """

        wr = np.zeros_like(self.invProb.model)

        for prob_JtJ, prob in zip(self.JtJdiag, self.prob):

            wr += prob_JtJ + self.threshold

        wr = wr**0.5
        wr /= wr.max()

        return wr

    def updateReg(self):
        """
            Update the cell weights with the approximated sensitivity
        """

        for reg in self.reg.objfcts:
            reg.cell_weights = reg.mapping * (self.wr)

    def updateOpt(self):
        """
            Update a copy of JtJdiag to optimization for preconditioner
        """
        # if self.ComboMisfitFun:
        JtJdiag = np.zeros_like(self.invProb.model)
        for prob, JtJ, dmisfit in zip(self.prob, self.JtJdiag, self.dmisfit.objfcts):

            JtJdiag += JtJ

        self.opt.JtJdiag = JtJdiag


class Update_DC_Wr(InversionDirective):
    """
        Update the sensitivity wegithing for the DC2D problem
    """

    wrType = 'sensitivityW'
    changeMref = False
    eps = 1e-8

    def initialize(self):

        m = self.invProb.model
        Jmat = self.prob[0].getJ(m, self.prob[0].fields(m))

        if self.wrType == 'sensitivityW':
            wr = np.sum((Jmat)**2., axis=0)**0.5 + self.eps
            wr = wr / wr.max()
            # for reg in self.reg.objfcts:
            self.reg.objfcts[0].cell_weights = self.reg.mapping * wr

        # self.opt.approxHinv = None
        JtJdiag = np.sum((self.dmisfit.W * Jmat)**2., axis=0)

        # Create the pre-conditioner
        self.regDiag = np.zeros_like(self.invProb.model)

        for reg in self.reg.objfcts:
            # Check if he has wire
            if getattr(reg.mapping, 'P', None) is None:
                self.regDiag += (reg.W.T * reg.W).diagonal()
            else:
                # He is a snitch!
                self.regDiag += reg.mapping.P.T * (reg.W.T * reg.W).diagonal()

        diagA = JtJdiag + self.invProb.beta * self.regDiag

        PC = Utils.sdiag((diagA)**-1.)
        self.opt.approxHinv = PC

    def endIter(self):

        m = self.invProb.model
        Jmat = self.prob[0].getJ(m, self.prob[0].fields(m))

        wr = np.sum((Jmat)**2. + self.eps**2., axis=0)**0.5
        wr = wr / wr.max()
        if self.wrType == 'sensitivityW':
            # for reg in self.reg.objfcts:
            self.reg.objfcts[0].cell_weights = self.reg.mapping * wr

        if self.changeMref:

            # mref = np.median(m)
            mref = np.sum(wr * m) / np.sum(wr)
            # print(self.reg.objfcts[0].cell_weights.min())

            print('Updating mref:' + str(np.exp(mref)) + 'S/m')

            for reg in self.reg.objfcts:
                reg.mref = np.ones_like(m) * mref

        # self.opt.approxHinv = None
        JtJdiag = np.sum((self.dmisfit.W * Jmat)**2., axis=0)

        # Create the pre-conditioner
        self.regDiag = np.zeros_like(self.invProb.model)

        for reg in self.reg.objfcts:
            # Check if he has wire
            if getattr(reg.mapping, 'P', None) is None:
                self.regDiag += (reg.W.T * reg.W).diagonal()
            else:
                # He is a snitch!
                self.regDiag += reg.mapping.P.T * (reg.W.T * reg.W).diagonal()

        diagA = JtJdiag + self.invProb.beta * self.regDiag

        PC = Utils.sdiag((diagA)**-1.)
        self.opt.approxHinv = PC
