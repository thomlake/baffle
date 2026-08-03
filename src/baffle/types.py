from collections.abc import Mapping

type Vec2 = tuple[int, int]

type Scalar = None | bool | int | str
type ComponentValue = Scalar | Vec2

type ComponentDict = Mapping[str, ComponentValue]
type StateDict = Mapping[str, ComponentDict]
