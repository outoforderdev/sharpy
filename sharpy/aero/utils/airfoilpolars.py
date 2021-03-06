import numpy as np
# import sharpy.utils.algebra as algebra
from sharpy.utils.constants import deg2rad


class polar(object):

    def __init__(self):

        self.table = None
        self.aoa_cl0_deg = None

    def initialise(self, table):

        # Store the table
        if (np.diff(table[:, 0]) > 0.).all():
            self.table = table
        else:
            raise RuntimeError("ERROR: angles of attack not ordered")

        # Look for aoa where CL=0
        npoints = self.table.shape[0]
        matches = []
        for ipoint in range(npoints - 1):
            if self.table[ipoint, 1] == 0.:
                matches.append(self.table[ipoint, 0])
            elif (self.table[ipoint, 1] < 0. and self.table[ipoint + 1, 1] > 0):
            # elif ((self.table[ipoint, 1] < 0. and self.table[ipoint + 1, 1] > 0) or
            #       (self.table[ipoint, 1] > 0. and self.table[ipoint + 1, 1] < 0)):
                if (self.table[ipoint, 0] <= 0.):
                    matches.append(np.interp(0,
                                             self.table[ipoint:ipoint+2, 1],
                                             self.table[ipoint:ipoint+2, 0]))
                # else:
                #     print("WARNING: Be careful negative camber airfoil not supported")

        iaoacl0 = 0
        aux = np.abs(matches[0])
        for imin in range(len(matches)):
            if np.abs(matches[imin]) < aux:
                aux = np.abs(matches[imin])
                iaoacl0 = imin
        self.aoa_cl0_deg = matches[iaoacl0]

    def get_coefs(self, aoa_deg):

        cl = np.interp(aoa_deg, self.table[:, 0], self.table[:, 1])
        cd = np.interp(aoa_deg, self.table[:, 0], self.table[:, 2])
        cm = np.interp(aoa_deg, self.table[:, 0], self.table[:, 3])

        return cl, cd, cm

    def get_aoa_deg_from_cl_2pi(self, cl):

        return cl/2/np.pi/deg2rad + self.aoa_cl0_deg

    def redefine_aoa(self, new_aoa):

        naoa = len(new_aoa)
        # Generate the same polar interpolated at different angles of attack
        # by linear interpolation
        table = np.zeros((naoa, 4))
        table[:, 0] = new_aoa
        for icol in range(1, 4):
            table[:, icol] = np.interp(table[:, 0],
                                       self.table[:, 0],
                                       self.table[:, icol])

        new_polar = polar()
        new_polar.initialise(table)
        return new_polar


def interpolate(polar1, polar2, coef=0.5):

    all_aoa = np.sort(np.concatenate((polar1.table[:, 0], polar2.table[:, 0]),))

    different_aoa = []
    different_aoa.append(all_aoa[0])
    for iaoa in range(1, len(all_aoa)):
        if not all_aoa[iaoa] == different_aoa[-1]:
            different_aoa.append(all_aoa[iaoa])

    new_polar1 = polar1.redefine_aoa(different_aoa)
    new_polar2 = polar2.redefine_aoa(different_aoa)

    table = (1. - coef)*new_polar1.table + coef*new_polar2.table

    new_polar = polar()
    new_polar.initialise(table)
    return new_polar
