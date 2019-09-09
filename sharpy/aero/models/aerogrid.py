"""Aerogrid

Aerogrid contains all the necessary routines to generate an aerodynamic
grid based on the input dictionaries.
"""
# Alfonso del Carre

# alfonso.del-carre14@imperial.ac.uk
# Imperial College London
# LoCA lab
# 29 Sept 2016


import ctypes as ct
import warnings

import numpy as np
import scipy.interpolate

import sharpy.utils.algebra as algebra
import sharpy.utils.cout_utils as cout
from sharpy.utils.datastructures import AeroTimeStepInfo
import sharpy.utils.generator_interface as gen_interface


class Aerogrid(object):
    def __init__(self):
        self.aero_dict = None
        self.beam = None
        self.aero_settings = None

        self.timestep_info = []
        self.ini_info = None

        self.surface_distribution = None
        self.surface_m = None
        self.aero_dimensions = None
        self.aero_dimensions_star = None
        self.airfoil_db = dict()
        self.struct2aero_mapping = None
        self.aero2struct_mapping = []

        self.n_node = 0
        self.n_elem = 0
        self.n_surf = 0
        self.n_aero_node = 0
        self.n_control_surfaces = 0

        self.cs_generators = []

    def generate(self, aero_dict, beam, aero_settings, ts):
        self.aero_dict = aero_dict
        self.beam = beam
        self.aero_settings = aero_settings

        # number of total nodes (structural + aero&struc)
        self.n_node = len(aero_dict['aero_node'])
        # number of elements
        self.n_elem = len(aero_dict['surface_distribution'])
        # surface distribution
        self.surface_distribution = aero_dict['surface_distribution']
        # number of surfaces
        temp = set(aero_dict['surface_distribution'])
        self.n_surf = sum(1 for i in temp if i >= 0)
        # number of chordwise panels
        self.surface_m = aero_dict['surface_m']
        # number of aero nodes
        self.n_aero_node = sum(aero_dict['aero_node'])

        # get N per surface
        self.calculate_dimensions()

        # write grid info to screen
        self.output_info()

        # allocating initial grid storage
        self.ini_info = AeroTimeStepInfo(self.aero_dimensions,
                                         self.aero_dimensions_star)

        # load airfoils db
        # for i_node in range(self.n_node):
        for i_elem in range(self.n_elem):
            for i_local_node in range(self.beam.num_node_elem):
                try:
                    self.airfoil_db[self.aero_dict['airfoil_distribution'][i_elem, i_local_node]]
                except KeyError:
                    airfoil_coords = self.aero_dict['airfoils'][str(self.aero_dict['airfoil_distribution'][i_elem, i_local_node])]
                    self.airfoil_db[self.aero_dict['airfoil_distribution'][i_elem, i_local_node]] = (
                        scipy.interpolate.interp1d(airfoil_coords[:, 0],
                                                   airfoil_coords[:, 1],
                                                   kind='quadratic',
                                                   copy=False,
                                                   fill_value='extrapolate',
                                                   assume_sorted=True))
        try:
            self.n_control_surfaces = np.sum(np.unique(self.aero_dict['control_surface']) >= 0)
        except KeyError:
            pass

        # Backward compatibility: check whether control surface deflection settings have been specified. If not, create
        # section with empty list, such that no cs generator is appended
        try:
            aero_settings['control_surface_deflection']
        except KeyError:
            aero_settings.update({'control_surface_deflection': ['']*self.n_control_surfaces})

        # initialise generators
        for i_cs in range(self.n_control_surfaces):
            if aero_settings['control_surface_deflection'][i_cs] == '':
                self.cs_generators.append(None)
            else:
                generator_type = gen_interface.generator_from_string(
                    aero_settings['control_surface_deflection'][i_cs])
                self.cs_generators.append(generator_type())
                self.cs_generators[i_cs].initialise(
                    aero_settings['control_surface_deflection_generator_settings'][i_cs])

        self.add_timestep()
        self.generate_mapping()
        self.generate_zeta(self.beam, self.aero_settings, ts)

    def output_info(self):
        cout.cout_wrap('The aerodynamic grid contains %u surfaces' % self.n_surf, 1)
        for i_surf in range(self.n_surf):
            cout.cout_wrap('  Surface %u, M=%u, N=%u' % (i_surf,
                                                         self.aero_dimensions[i_surf, 0],
                                                         self.aero_dimensions[i_surf, 1]), 1)
            cout.cout_wrap('     Wake %u, M=%u, N=%u' % (i_surf,
                                                         self.aero_dimensions_star[i_surf, 0],
                                                         self.aero_dimensions_star[i_surf, 1]))
        cout.cout_wrap('  In total: %u bound panels' % (sum(self.aero_dimensions[:, 0]*
                                                            self.aero_dimensions[:, 1])))
        cout.cout_wrap('  In total: %u wake panels' % (sum(self.aero_dimensions_star[:, 0]*
                                                           self.aero_dimensions_star[:, 1])))
        cout.cout_wrap('  Total number of panels = %u' % (sum(self.aero_dimensions[:, 0]*
                                                              self.aero_dimensions[:, 1]) +
                                                          sum(self.aero_dimensions_star[:, 0]*
                                                              self.aero_dimensions_star[:, 1])))

    def calculate_dimensions(self):
        self.aero_dimensions = np.zeros((self.n_surf, 2), dtype=int)
        for i in range(self.n_surf):
            # adding M values
            self.aero_dimensions[i, 0] = self.surface_m[i]
        # count N values (actually, the count result
        # will be N+1)
        nodes_in_surface = []
        for i_surf in range(self.n_surf):
            nodes_in_surface.append([])
        for i_elem in range(self.beam.num_elem):
            nodes = self.beam.elements[i_elem].global_connectivities
            i_surf = self.aero_dict['surface_distribution'][i_elem]
            if i_surf < 0:
                continue
            for i_global_node in nodes:
                if i_global_node in nodes_in_surface[i_surf]:
                    continue
                else:
                    nodes_in_surface[i_surf].append(i_global_node)
                if self.aero_dict['aero_node'][i_global_node]:
                    self.aero_dimensions[i_surf, 1] += 1

        # accounting for N+1 nodes -> N panels
        self.aero_dimensions[:, 1] -= 1

        self.aero_dimensions_star = self.aero_dimensions.copy()
        for i_surf in range(self.n_surf):
            self.aero_dimensions_star[i_surf, 0] = self.aero_settings['mstar'].value

    def add_timestep(self):
        try:
            self.timestep_info.append(self.timestep_info[-1].copy())
        except IndexError:
            self.timestep_info.append(self.ini_info.copy())

    def generate_zeta_timestep_info(self, structure_tstep, aero_tstep, beam, aero_settings, it=None, dt=None):
        if it is None:
            it = len(beam.timestep_info) - 1
        global_node_in_surface = []
        for i_surf in range(self.n_surf):
            global_node_in_surface.append([])

        # check that we have control surface information
        try:
            self.aero_dict['control_surface']
            with_control_surfaces = True
        except KeyError:
            with_control_surfaces = False

        # check that we have sweep information
        try:
            self.aero_dict['sweep']
        except KeyError:
            self.aero_dict['sweep'] = np.zeros_like(self.aero_dict['twist'])

        # one surface per element
        for i_elem in range(self.n_elem):
            i_surf = self.aero_dict['surface_distribution'][i_elem]
            # check if we have to generate a surface here
            if i_surf == -1:
                continue

            for i_local_node in range(len(self.beam.elements[i_elem].global_connectivities)):
                i_global_node = self.beam.elements[i_elem].global_connectivities[i_local_node]
                # i_global_node = self.beam.elements[i_elem].global_connectivities[
                #     self.beam.elements[i_elem].ordering[i_local_node]]
                if not self.aero_dict['aero_node'][i_global_node]:
                    continue
                if i_global_node in global_node_in_surface[i_surf]:
                    continue
                else:
                    global_node_in_surface[i_surf].append(i_global_node)

                # master_elem, master_elem_node = beam.master[i_elem, i_local_node, :]
                # if master_elem < 0:
                    # master_elem = i_elem
                    # master_elem_node = i_local_node

                # find the i_surf and i_n data from the mapping
                i_n = -1
                ii_surf = -1
                for i in range(len(self.struct2aero_mapping[i_global_node])):
                    i_n = self.struct2aero_mapping[i_global_node][i]['i_n']
                    ii_surf = self.struct2aero_mapping[i_global_node][i]['i_surf']
                    if ii_surf == i_surf:
                        break
                # make sure it found it
                if i_n == -1 or ii_surf == -1:
                    raise AssertionError('Error 12958: Something failed with the mapping in aerogrid.py. Check/report!')

                # control surface implementation
                control_surface_info = None
                if with_control_surfaces:
                # 1) check that this node and elem have a control surface
                    if self.aero_dict['control_surface'][i_elem, i_local_node] >= 0:
                        i_control_surface = self.aero_dict['control_surface'][i_elem, i_local_node]
                # 2) type of control surface + write info
                        control_surface_info = dict()
                        if self.aero_dict['control_surface_type'][i_control_surface] == 0:
                            control_surface_info['type'] = 'static'
                            control_surface_info['deflection'] = self.aero_dict['control_surface_deflection'][i_control_surface]
                            control_surface_info['chord'] = self.aero_dict['control_surface_chord'][i_control_surface]
                            try:
                                control_surface_info['hinge_coords'] = self.aero_dict['control_surface_hinge_coords'][i_control_surface]
                            except KeyError:
                                control_surface_info['hinge_coords'] = None
                        elif self.aero_dict['control_surface_type'][i_control_surface] == 1:
                            control_surface_info['type'] = 'dynamic'
                            control_surface_info['chord'] = self.aero_dict['control_surface_chord'][i_control_surface]
                            try:
                                control_surface_info['hinge_coords'] = self.aero_dict['control_surface_hinge_coords'][i_control_surface]
                            except KeyError:
                                control_surface_info['hinge_coords'] = None

                            params = {'it': it}
                            control_surface_info['deflection'], control_surface_info['deflection_dot'] = \
                                self.cs_generators[i_control_surface](params)

                        elif self.aero_dict['control_surface_type'][i_control_surface] == 2:
                            control_surface_info['type'] = 'controlled'

                            try:
                                old_deflection = self.data.aero.timestep_info[-1].control_surface_deflection[i_control_surface]
                            except AttributeError:
                                try:
                                    old_deflection = aero_tstep.control_surface_deflection[i_control_surface]
                                except IndexError:
                                    old_deflection = self.aero_dict['control_surface_deflection'][i_control_surface]

                            try:
                                control_surface_info['deflection'] = aero_tstep.control_surface_deflection[i_control_surface]
                            except IndexError:
                                control_surface_info['deflection'] = self.aero_dict['control_surface_deflection'][i_control_surface]

                            if dt is not None:
                                control_surface_info['deflection_dot'] = (
                                        (control_surface_info['deflection'] - old_deflection)/dt)
                            else:
                                control_surface_info['deflection_dot'] = 0.0

                            control_surface_info['chord'] = self.aero_dict['control_surface_chord'][i_control_surface]

                            try:
                                control_surface_info['hinge_coords'] = self.aero_dict['control_surface_hinge_coords'][i_control_surface]
                            except KeyError:
                                control_surface_info['hinge_coords'] = None
                        else:
                            raise NotImplementedError(str(self.aero_dict['control_surface_type'][i_control_surface]) +
                                ' control surfaces are not yet implemented')



                node_info = dict()
                node_info['i_node'] = i_global_node
                node_info['i_local_node'] = i_local_node
                node_info['chord'] = self.aero_dict['chord'][i_elem, i_local_node]
                node_info['eaxis'] = self.aero_dict['elastic_axis'][i_elem, i_local_node]
                node_info['twist'] = self.aero_dict['twist'][i_elem, i_local_node]
                node_info['sweep'] = self.aero_dict['sweep'][i_elem, i_local_node]
                node_info['M'] = self.aero_dimensions[i_surf, 0]
                node_info['M_distribution'] = self.aero_dict['m_distribution'].decode('ascii')
                node_info['airfoil'] = self.aero_dict['airfoil_distribution'][i_elem, i_local_node]
                node_info['control_surface'] = control_surface_info
                node_info['beam_coord'] = structure_tstep.pos[i_global_node, :]
                node_info['pos_dot'] = structure_tstep.pos_dot[i_global_node, :]
                node_info['beam_psi'] = structure_tstep.psi[i_elem, i_local_node, :]
                node_info['psi_dot'] = structure_tstep.psi_dot[i_elem, i_local_node, :]
                node_info['for_delta'] = beam.frame_of_reference_delta[i_elem, i_local_node, :]
                node_info['elem'] = beam.elements[i_elem]
                node_info['for_pos'] = structure_tstep.for_pos
                node_info['cga'] = structure_tstep.cga()
                if node_info['M_distribution'].lower() == 'user_defined':
                    ielem_in_surf = i_elem - np.sum(self.surface_distribution < i_surf)
                    node_info['user_defined_m_distribution'] = self.aero_dict['user_defined_m_distribution'][str(i_surf)][:, ielem_in_surf, i_local_node]
                (aero_tstep.zeta[i_surf][:, :, i_n],
                 aero_tstep.zeta_dot[i_surf][:, :, i_n]) = (
                    generate_strip(node_info,
                                   self.airfoil_db,
                                   aero_settings['aligned_grid'],
                                   orientation_in=aero_settings['freestream_dir'],
                                   calculate_zeta_dot=True))

    def generate_zeta(self, beam, aero_settings, ts=-1, beam_ts=-1):
        self.generate_zeta_timestep_info(beam.timestep_info[beam_ts],
                                         self.timestep_info[ts],
                                         beam,
                                         aero_settings)

    def generate_mapping(self):
        self.struct2aero_mapping = [[]]*self.n_node
        surf_n_counter = np.zeros((self.n_surf,), dtype=int)
        nodes_in_surface = []
        for i_surf in range(self.n_surf):
            nodes_in_surface.append([])

        for i_elem in range(self.n_elem):
            i_surf = self.aero_dict['surface_distribution'][i_elem]
            if i_surf == -1:
                continue
            for i_global_node in self.beam.elements[i_elem].reordered_global_connectivities:
                if not self.aero_dict['aero_node'][i_global_node]:
                    continue

                if i_global_node in nodes_in_surface[i_surf]:
                    continue
                else:
                    nodes_in_surface[i_surf].append(i_global_node)
                    surf_n_counter[i_surf] += 1
                    try:
                        self.struct2aero_mapping[i_global_node][0]
                    except IndexError:
                        self.struct2aero_mapping[i_global_node] = []

                i_n = surf_n_counter[i_surf] - 1
                self.struct2aero_mapping[i_global_node].append({'i_surf': i_surf,
                                                                'i_n': i_n})

        nodes_in_surface = []
        for i_surf in range(self.n_surf):
            nodes_in_surface.append([])

        for i_surf in range(self.n_surf):
            self.aero2struct_mapping.append([-1]*(surf_n_counter[i_surf]))

        for i_elem in range(self.n_elem):
            for i_global_node in self.beam.elements[i_elem].global_connectivities:
                for i in range(len(self.struct2aero_mapping[i_global_node])):
                    try:
                        i_surf = self.struct2aero_mapping[i_global_node][i]['i_surf']
                        i_n = self.struct2aero_mapping[i_global_node][i]['i_n']
                        if i_global_node in nodes_in_surface[i_surf]:
                            continue
                        else:
                            nodes_in_surface[i_surf].append(i_global_node)
                    except KeyError:
                        continue
                    self.aero2struct_mapping[i_surf][i_n] = i_global_node

    def update_orientation(self, quat, ts=-1):
        rot = algebra.quat2rotation(quat)
        self.timestep_info[ts].update_orientation(rot.T)

    @staticmethod
    def compute_gamma_dot(dt, tstep, previous_tsteps):
        r"""
        Computes the temporal derivative of circulation (gamma) using finite differences.

        It will use a first order approximation for the first evaluation
        (when ``len(previous_tsteps) == 1``), and then second order ones.

        .. math:: \left.\frac{d\Gamma}{dt}\right|^n \approx \lim_{\Delta t \rightarrow 0}\frac{\Gamma^n-\Gamma^{n-1}}{\Delta t}

        For the second time step and onwards, the following second order approximation is used:

        .. math:: \left.\frac{d\Gamma}{dt}\right|^n \approx \lim_{\Delta t \rightarrow 0}\frac{3\Gamma^n -4\Gamma^{n-1}+\Gamma^{n-2}}{2\Delta t}

        Args:
            dt (float): delta time for the finite differences
            tstep (AeroTimeStepInfo): tstep at time n (current)
            previous_tsteps (list(AeroTimeStepInfo)): previous tstep structure in order: ``[n-N,..., n-2, n-1]``

        Returns:
            float: first derivative of circulation with respect to time

        See Also:
            .. py:class:: sharpy.utils.datastructures.AeroTimeStepInfo
        """
        # Check whether the iteration is part of FSI (ie the input is a k-step) or whether it is an only aerodynamic
        # simulation
        part_of_fsi = True
        try:
            if tstep is previous_tsteps[-1]:
                part_of_fsi = False
        except IndexError:
            for i_surf in range(tstep.n_surf):
                tstep.gamma_dot[i_surf].fill(0.0)
            return

        if len(previous_tsteps) == 0:
            for i_surf in range(tstep.n_surf):
                tstep.gamma_dot[i_surf].fill(0.0)
        # elif len(previous_tsteps) == 1:
            # # first order
            # # f'(n) = (f(n) - f(n - 1))/dx
            # for i_surf in range(tstep.n_surf):
                # tstep.gamma_dot[i_surf] = (tstep.gamma[i_surf] - previous_tsteps[-1].gamma[i_surf])/dt
        # else:
            # # second order
            # for i_surf in range(tstep.n_surf):
                # if (not np.isfinite(tstep.gamma[i_surf]).any()) or \
                    # (not np.isfinite(previous_tsteps[-1].gamma[i_surf]).any()) or \
                        # (not np.isfinite(previous_tsteps[-2].gamma[i_surf]).any()):
                    # raise ArithmeticError('NaN found in gamma')

                # if part_of_fsi:
                    # tstep.gamma_dot[i_surf] = (3.0*tstep.gamma[i_surf]
                                               # - 4.0*previous_tsteps[-1].gamma[i_surf]
                                               # + previous_tsteps[-2].gamma[i_surf])/(2.0*dt)
                # else:
                    # tstep.gamma_dot[i_surf] = (3.0*tstep.gamma[i_surf]
                                               # - 4.0*previous_tsteps[-2].gamma[i_surf]
                                               # + previous_tsteps[-3].gamma[i_surf])/(2.0*dt)
        if part_of_fsi:
            for i_surf in range(tstep.n_surf):
                tstep.gamma_dot[i_surf] = (tstep.gamma[i_surf] - previous_tsteps[-1].gamma[i_surf])/dt
        else:
            for i_surf in range(tstep.n_surf):
                tstep.gamma_dot[i_surf] = (tstep.gamma[i_surf] - previous_tsteps[-2].gamma[i_surf])/dt



def generate_strip(node_info, airfoil_db, aligned_grid, orientation_in=np.array([1, 0, 0]), calculate_zeta_dot = False):
    """
    Returns a strip in "a" frame of reference, it has to be then rotated to
    simulate angles of attack, etc
    :param node_info:
    :param airfoil_db:
    :param aligned_grid:
    :param orientation_in:
    :return:
    """
    strip_coordinates_a_frame = np.zeros((3, node_info['M'] + 1), dtype=ct.c_double)
    strip_coordinates_b_frame = np.zeros((3, node_info['M'] + 1), dtype=ct.c_double)
    zeta_dot_a_frame = np.zeros((3, node_info['M'] + 1), dtype=ct.c_double)

    # airfoil coordinates
    # we are going to store everything in the x-z plane of the b
    # FoR, so that the transformation Cab rotates everything in place.
    if node_info['M_distribution'] == 'uniform':
        strip_coordinates_b_frame[1, :] = np.linspace(0.0, 1.0, node_info['M'] + 1)
    elif node_info['M_distribution'] == '1-cos':
        domain = np.linspace(0, 1.0, node_info['M'] + 1)
        strip_coordinates_b_frame[1, :] = 0.5*(1.0 - np.cos(domain*np.pi))
    elif node_info['M_distribution'].lower() == 'user_defined':
        # strip_coordinates_b_frame[1, :-1] = np.linspace(0.0, 1.0 - node_info['last_panel_length'], node_info['M'])
        # strip_coordinates_b_frame[1,-1] = 1.
        strip_coordinates_b_frame[1,:] = node_info['user_defined_m_distribution']
    else:
        raise NotImplemented('M_distribution is ' + node_info['M_distribution'] +
                             ' and it is not yet supported')
    strip_coordinates_b_frame[2, :] = airfoil_db[node_info['airfoil']](
                                            strip_coordinates_b_frame[1, :])

    # elastic axis correction
    for i_M in range(node_info['M'] + 1):
        strip_coordinates_b_frame[1, i_M] -= node_info['eaxis']

    # chord_line_b_frame = strip_coordinates_b_frame[:, -1] - strip_coordinates_b_frame[:, 0]
    cs_velocity = np.zeros_like(strip_coordinates_b_frame)

    # control surface deflection
    if node_info['control_surface'] is not None:
        b_frame_hinge_coords = strip_coordinates_b_frame[:, node_info['M'] - node_info['control_surface']['chord']]
        # support for different hinge location for fully articulated control surfaces
        if node_info['control_surface']['hinge_coords'] is not None:
            # make sure the hinge coordinates are only applied when M == cs_chord
            if not node_info['M'] - node_info['control_surface']['chord'] == 0:
                node_info['control_surface']['hinge_coords'] = None
            else:
                b_frame_hinge_coords =  node_info['control_surface']['hinge_coords']

        for i_M in range(node_info['M'] - node_info['control_surface']['chord'], node_info['M'] + 1):
            relative_coords = strip_coordinates_b_frame[:, i_M] - b_frame_hinge_coords
            # rotate the control surface
            relative_coords = np.dot(algebra.rotation3d_x(-node_info['control_surface']['deflection']),
                                     relative_coords)
            # deflection velocity
            try:
                cs_velocity[:, i_M] += np.cross(np.array([-node_info['control_surface']['deflection_dot'], 0.0, 0.0]),
                                                relative_coords)
            except KeyError:
                pass

            # restore coordinates
            relative_coords += b_frame_hinge_coords

            # substitute with new coordinates
            strip_coordinates_b_frame[:, i_M] = relative_coords

    # chord scaling
    strip_coordinates_b_frame *= node_info['chord']

    # twist transformation (rotation around x_b axis)
    if np.abs(node_info['twist']) > 1e-6:
        Ctwist = algebra.rotation3d_x(node_info['twist'])
    else:
        Ctwist = np.eye(3)

    # Cab transformation
    Cab = algebra.crv2rotation(node_info['beam_psi'])

    rot_angle = algebra.angle_between_vectors_sign(orientation_in, Cab[:, 1], Cab[:, 2])
    if np.sign(np.dot(orientation_in, Cab[:, 1])) >= 0:
        rot_angle = 0.0
    else:
        rot_angle = -np.pi
    Crot = algebra.rotation3d_z(-rot_angle)

    c_sweep = np.eye(3)
    if np.abs(node_info['sweep']) > 1e-6:
        c_sweep = algebra.rotation3d_z(node_info['sweep'])

    # transformation from beam to beam prime (with sweep and twist)
    for i_M in range(node_info['M'] + 1):
        strip_coordinates_b_frame[:, i_M] = np.dot(c_sweep, np.dot(Crot,
                                                   np.dot(Ctwist, strip_coordinates_b_frame[:, i_M])))
        strip_coordinates_a_frame[:, i_M] = np.dot(Cab, strip_coordinates_b_frame[:, i_M])

        cs_velocity[:, i_M] = np.dot(Cab, cs_velocity[:, i_M])

    # zeta_dot
    if calculate_zeta_dot:
        # velocity due to pos_dot
        for i_M in range(node_info['M'] + 1):
            zeta_dot_a_frame[:, i_M] += node_info['pos_dot']

        # velocity due to psi_dot
        omega_a = algebra.crv_dot2omega(node_info['beam_psi'], node_info['psi_dot'])
        for i_M in range(node_info['M'] + 1):
            zeta_dot_a_frame[:, i_M] += (
                np.dot(algebra.skew(omega_a), strip_coordinates_a_frame[:, i_M]))

        # control surface deflection velocity contribution
        try:
            if node_info['control_surface'] is not None:
                node_info['control_surface']['deflection_dot']
                for i_M in range(node_info['M'] + 1):
                    zeta_dot_a_frame[:, i_M] += cs_velocity[:, i_M]
        except KeyError:
            pass

    else:
        zeta_dot_a_frame = np.zeros((3, node_info['M'] + 1), dtype=ct.c_double)

    # add node coords
    for i_M in range(node_info['M'] + 1):
        strip_coordinates_a_frame[:, i_M] += node_info['beam_coord']

    # add quarter-chord disp
    delta_c = (strip_coordinates_a_frame[:, -1] - strip_coordinates_a_frame[:, 0])/node_info['M']
    if node_info['M_distribution'] == 'uniform':
        for i_M in range(node_info['M'] + 1):
                strip_coordinates_a_frame[:, i_M] += 0.25*delta_c
    else:
        warnings.warn("No quarter chord disp of grid for non-uniform grid distributions implemented", UserWarning)

    # rotation from a to g
    for i_M in range(node_info['M'] + 1):
        strip_coordinates_a_frame[:, i_M] = np.dot(node_info['cga'],
                                                   strip_coordinates_a_frame[:, i_M])
        zeta_dot_a_frame[:, i_M] = np.dot(node_info['cga'],
                                          zeta_dot_a_frame[:, i_M])

    return strip_coordinates_a_frame, zeta_dot_a_frame
