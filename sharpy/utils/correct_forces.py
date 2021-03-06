"""
Force correction utilities

The aerodynamic forces can be corrected with these functions.
The correction is done once they are projected on the structural beam.

Args:
    data (:class:`sharpy.PreSharpy`): SHARPy data
    aero_kstep (:class:`sharpy.utils.datastructures.AeroTimeStepInfo`): Current aerodynamic substep
    structural_kstep (:class:`sharpy.utils.datastructures.StructTimeStepInfo`): Current structural substep
    struct_forces (np.array): Array with the aerodynamic forces mapped on the structure in the B frame of reference

Returns:
    new_struct_forces (np.array): Array with the corrected forces
"""
import numpy as np

import sharpy.aero.utils.airfoilpolars as ap
import sharpy.utils.algebra as algebra
from sharpy.utils.constants import deg2rad


# dict_of_corrections = {}
# # Decorator
# def gen_dict_force_corrections(func):
#     global dict_of_corrections
#     dict_of_corrections[func.__name__] = func

# @gen_dict_force_corrections
def efficiency(data, aero_kstep, structural_kstep, struct_forces):
    r"""
    The efficiency and constant terms are introduced by means of the array ``airfoil_efficiency`` in the ``aero.h5``

    .. math::
        \mathbf{f}_{struct}^B &= \varepsilon^f_0 \mathbf{f}_{i,struct}^B + \varepsilon^f_1\\
        \mathbf{m}_{struct}^B &= \varepsilon^m_0 \mathbf{m}_{i,struct}^B + \varepsilon^m_1

    Notice that the moment correction is applied on top of the force correction. As a consequence, the aerodynamic
    moments generated by the forces on the vortices are corrected sequently by both efficiencies.

    Args:
        local_aero_forces (np.ndarray): aerodynamic forces and moments at a grid vertex
        chi_g (np.ndarray): vector between grid vertex and structural node in inertial frame
        cbg (np.ndarray): transformation matrix between inertial and body frames of reference
        force_efficiency (np.ndarray): force efficiency matrix for all structural elements. Its size is ``n_elem x n_node_elem x 2 x 3``
        moment_efficiency (np.ndarray): moment efficiency matrix for all structural elements. Its size is ``n_elem x n_node_elem x 2 x 3``
        i_elem (int): element index
        i_local_node (int): local node index within element

    Returns:
         np.ndarray: corresponding aerodynamic force at the structural node from the force and moment at a grid vertex
    """

    n_node = data.structure.num_node
    n_elem = data.structure.num_elem
    aero_dict = data.aero.aero_dict
    new_struct_forces = np.zeros_like(struct_forces)

    # load airfoil efficiency (if it exists); else set to one (to avoid multiple ifs in the loops)
    airfoil_efficiency = aero_dict['airfoil_efficiency']
    # force efficiency dimensions [n_elem, n_node_elem, 2, [fx, fy, fz]] - all defined in B frame
    force_efficiency = np.zeros((n_elem, 3, 2, 3))
    force_efficiency[:, :, 0, :] = 1.
    force_efficiency[:, :, :, 1] = airfoil_efficiency[:, :, :, 0]
    force_efficiency[:, :, :, 2] = airfoil_efficiency[:, :, :, 1]

    # moment efficiency dimensions [n_elem, n_node_elem, 2, [mx, my, mz]] - all defined in B frame
    moment_efficiency = np.zeros((n_elem, 3, 2, 3))
    moment_efficiency[:, :, 0, :] = 1.
    moment_efficiency[:, :, :, 0] = airfoil_efficiency[:, :, :, 2]

    for inode in range(n_node):
        i_elem, i_local_node = data.structure.node_master_elem[inode]
        new_struct_forces[inode, :] = struct_forces[inode, :].copy()
        new_struct_forces[inode, 0:3] *= force_efficiency[i_elem, i_local_node, 0, :] # element wise multiplication
        new_struct_forces[inode, 0:3] += force_efficiency[i_elem, i_local_node, 1, :]
        new_struct_forces[inode, 3:6] *= moment_efficiency[i_elem, i_local_node, 0, :]
        new_struct_forces[inode, 3:6] += moment_efficiency[i_elem, i_local_node, 1, :]
    return new_struct_forces

# @gen_dict_force_corrections
def polars(data, aero_kstep, structural_kstep, struct_forces):
    r"""
    This function corrects the aerodynamic forces from UVLM based on the airfoil polars provided by the user in the aero.h5 file

    These are the steps needed to correct the forces:
    
        * The force coming from UVLM is divided into induced drag (parallel to the incoming flow velocity) and lift (the remaining force).
        * The angle of attack is computed based on that lift force and the angle of zero lift computed form the airfoil polar and assuming a slope of :math:`2 \pi`
        * The dreag force is computed based on the angle of attack and the polars provided by the user
    """

    aerogrid = data.aero
    beam = data.structure
    rho = 1.225
    aero_dict = aerogrid.aero_dict
    if aerogrid.polars is None:
        return struct_forces
    new_struct_forces = np.zeros_like(struct_forces)

    nnode = struct_forces.shape[0]
    for inode in range(nnode):
        new_struct_forces[inode, :] = struct_forces[inode, :].copy()
        if aero_dict['aero_node'][inode]:

            ielem, inode_in_elem = beam.node_master_elem[inode]
            iairfoil = aero_dict['airfoil_distribution'][ielem, inode_in_elem]
            isurf = aerogrid.struct2aero_mapping[inode][0]['i_surf']
            i_n = aerogrid.struct2aero_mapping[inode][0]['i_n']
            N = aerogrid.aero_dimensions[isurf, 1]
            polar = aerogrid.polars[iairfoil]
            cab = algebra.crv2rotation(structural_kstep.psi[ielem, inode_in_elem, :])
            cga = algebra.quat2rotation(structural_kstep.quat)
            cgb = np.dot(cga, cab)

            # Deal with the extremes
            if i_n == 0:
                node1 = 0
                node2 = 1
            elif i_n == N:
                node1 = nnode - 1
                node2 = nnode - 2
            else:
                node1 = inode + 1
                node2 = inode - 1

            # Define the span and the span direction
            dir_span = 0.5*np.dot(cga,
                              structural_kstep.pos[node1, :] - structural_kstep.pos[node2, :])
            span = np.linalg.norm(dir_span)
            dir_span = algebra.unit_vector(dir_span)

            # Define the chord and the chord direction
            dir_chord = aero_kstep.zeta[isurf][:, -1, i_n] - aero_kstep.zeta[isurf][:, 0, i_n]
            chord = np.linalg.norm(dir_chord)
            dir_chord = algebra.unit_vector(dir_chord)

            # Define the relative velocity and its direction
            urel = (structural_kstep.pos_dot[inode, :] +
                    structural_kstep.for_vel[0:3] +
                    np.cross(structural_kstep.for_vel[3:6],
                             structural_kstep.pos[inode, :]))
            urel = -np.dot(cga, urel)
            urel += np.average(aero_kstep.u_ext[isurf][:, :, i_n], axis=1)
            # uind = uvlmlib.uvlm_calculate_total_induced_velocity_at_points(aero_kstep,
            #                                                                np.array([structural_kstep.pos[inode, :] - np.array([0, 0, 1])]),
            #                                                                structural_kstep.for_pos,
            #                                                                ct.c_uint(8))[0]
            # print(inode, urel, uind)
            # urel -= uind
            dir_urel = algebra.unit_vector(urel)


            # Force in the G frame of reference
            force = np.dot(cgb,
                           struct_forces[inode, 0:3])
            dir_force = algebra.unit_vector(force)

            # Coefficient to change from aerodynamic coefficients to forces (and viceversa)
            coef = 0.5*rho*np.linalg.norm(urel)**2*chord*span

            # Divide the force in drag and lift
            drag_force = np.dot(force, dir_urel)*dir_urel
            lift_force = force - drag_force

            # Compute the associated lift
            cl = np.linalg.norm(lift_force)/coef

            # Compute the angle of attack assuming that UVLM giveas a 2pi polar
            aoa_deg_2pi = polar.get_aoa_deg_from_cl_2pi(cl)

            # Compute the coefficients assocaited to that angle of attack
            cl_new, cd, cm = polar.get_coefs(aoa_deg_2pi)
            # print(cl, cl_new)

            # Recompute the forces based on the coefficients
            lift_force = cl*algebra.unit_vector(lift_force)*coef
            drag_force += cd*dir_urel*coef
            force = lift_force + drag_force
            new_struct_forces[inode, 0:3] = np.dot(cgb.T,
                                               force)

    return new_struct_forces

# TODO: the idea of the decorator is better. However, this is the only way I
# found to make this appear in the documentation
dict_of_corrections = {'efficiency': efficiency,
                       'polars': polars}
