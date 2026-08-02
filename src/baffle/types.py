from collections.abc import Mapping

type Vec2 = tuple[int, int]

type Scalar = None | bool | int | str
type ComponentValue = Scalar | Vec2

type Components = Mapping[str, ComponentValue]
type State = Mapping[str, Components]
