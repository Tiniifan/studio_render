"""The few parts of Blender's mathutils that studio_eleven's readers use, in numpy.

Installed as the `mathutils` module only when Blender's one is missing. It follows the Blender conventions
(Matrix built from rows, Quaternion stored as w, x, y, z) so the readers give the same values as in Blender.
"""
import math

import numpy as np


class Vector:
    def __init__(self, values=(0.0, 0.0, 0.0)):
        self._v = np.array(values, dtype=np.float64)

    def __len__(self):
        return len(self._v)

    def __iter__(self):
        return iter(float(v) for v in self._v)

    def __getitem__(self, index):
        return float(self._v[index])

    def __setitem__(self, index, value):
        self._v[index] = value

    @property
    def x(self):
        return float(self._v[0])

    @property
    def y(self):
        return float(self._v[1])

    @property
    def z(self):
        return float(self._v[2])

    def __add__(self, other):
        return Vector(self._v + np.asarray(list(other), dtype=np.float64))

    def __sub__(self, other):
        return Vector(self._v - np.asarray(list(other), dtype=np.float64))

    def __mul__(self, value):
        return Vector(self._v * value)

    def __repr__(self):
        return f"Vector({tuple(self)})"


class Quaternion:
    def __init__(self, values=(1.0, 0.0, 0.0, 0.0)):
        self._q = np.array(values, dtype=np.float64)

    def __iter__(self):
        return iter(float(v) for v in self._q)

    def __getitem__(self, index):
        return float(self._q[index])

    def __len__(self):
        return 4

    @property
    def w(self):
        return float(self._q[0])

    @property
    def x(self):
        return float(self._q[1])

    @property
    def y(self):
        return float(self._q[2])

    @property
    def z(self):
        return float(self._q[3])

    def inverted(self):
        w, x, y, z = self._q
        norm = w * w + x * x + y * y + z * z
        if norm <= 0.0:
            return Quaternion((1.0, 0.0, 0.0, 0.0))
        return Quaternion((w / norm, -x / norm, -y / norm, -z / norm))

    def normalized(self):
        length = np.linalg.norm(self._q)
        return Quaternion(self._q / length if length > 0.0 else (1.0, 0.0, 0.0, 0.0))

    def to_matrix(self):
        w, x, y, z = self.normalized()._q
        return Matrix([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ])

    def __repr__(self):
        return f"Quaternion({tuple(self)})"


class Euler:
    def __init__(self, values=(0.0, 0.0, 0.0), order="XYZ"):
        self._e = np.array(values, dtype=np.float64)
        self.order = order

    def __iter__(self):
        return iter(float(v) for v in self._e)


class Color:
    def __init__(self, values=(0.0, 0.0, 0.0)):
        self._c = np.array(values, dtype=np.float64)

    def __iter__(self):
        return iter(float(v) for v in self._c)


class Matrix:
    def __init__(self, rows=None):
        self._m = np.identity(4) if rows is None else np.array([list(row) for row in rows], dtype=np.float64)

    @classmethod
    def Identity(cls, size):
        return cls(np.identity(size))

    @property
    def array(self):
        return self._m

    def __len__(self):
        return self._m.shape[0]

    def __getitem__(self, index):
        return Vector(self._m[index])

    def __iter__(self):
        return iter(Vector(row) for row in self._m)

    def __matmul__(self, other):
        if isinstance(other, Matrix):
            return Matrix(self._m @ other._m)
        values = np.asarray(list(other), dtype=np.float64)
        size = self._m.shape[0]
        if len(values) == size - 1:
            return Vector((self._m @ np.append(values, 1.0))[:-1])
        return Vector(self._m @ values)

    def transposed(self):
        return Matrix(self._m.T)

    def inverted(self):
        return Matrix(np.linalg.inv(self._m))

    def to_3x3(self):
        return Matrix(self._m[:3, :3])

    def to_4x4(self):
        matrix = np.identity(4)
        size = min(self._m.shape[0], 4)
        matrix[:size, :size] = self._m[:size, :size]
        return Matrix(matrix)

    @property
    def translation(self):
        return Vector(self._m[:3, 3])

    @translation.setter
    def translation(self, value):
        self._m[:3, 3] = list(value)[:3]

    def to_quaternion(self):
        m = self._m[:3, :3].copy()
        # Blender normalizes the axes first
        for column in range(3):
            length = np.linalg.norm(m[:, column])
            if length > 0.0:
                m[:, column] /= length
        trace = m[0, 0] + m[1, 1] + m[2, 2]
        if trace > 0.0:
            s = 2.0 * math.sqrt(1.0 + trace)
            w, x, y, z = 0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s
        elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
            s = 2.0 * math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2])
            w, x, y, z = (m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s
        elif m[1, 1] > m[2, 2]:
            s = 2.0 * math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2])
            w, x, y, z = (m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s
        else:
            s = 2.0 * math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1])
            w, x, y, z = (m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s
        if w < 0.0:
            w, x, y, z = -w, -x, -y, -z
        return Quaternion((w, x, y, z))

    def __repr__(self):
        return f"Matrix({self._m.tolist()})"
