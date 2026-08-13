from ._base_task import Base_Task
from .utils import *
import numpy as np
import sapien


class move_object_between_zones(Base_Task):

    def setup_demo(self, **kwags):
        super()._init_task_env_(**kwags)

    def load_actors(self):
        self.regions = {}
        self._initial_positions = {}

        self.green_block = create_box(
            scene=self,
            pose=rand_pose(xlim=[-0.2, -0.1], ylim=[-0.05, 0.08], zlim=[0.766, 0.766], qpos=[1, 0, 0, 0], rotate_rand=True, rotate_lim=[0, 0, 0.4]),
            half_size=(0.025, 0.025, 0.025),
            color=(0.0, 0.8, 0.1),
            name="green_block",
            is_static=False,
        )
        self.green_block.set_mass(0.05)
        self.add_prohibit_area(self.green_block, padding=0.07)
        self._initial_positions["green_block"] = self.green_block.get_pose().p.copy()

        self.blue_bowl = create_actor(
            scene=self,
            pose=sapien.Pose([0.24, 0.06, 0.76], [0.5, 0.5, 0.5, 0.5]),
            modelname="002_bowl",
            convex=True,
            model_id=3,
        )
        self.blue_bowl.set_mass(0.05)
        self.add_prohibit_area(self.blue_bowl, padding=0.06)
        self._initial_positions["blue_bowl"] = self.blue_bowl.get_pose().p.copy()

        self.left_zone_marker = create_box(
            scene=self,
            pose=sapien.Pose([-0.15, -0.12, 0.742], [1, 0, 0, 0]),
            half_size=(0.08, 0.06, 0.002),
            color=(0.0, 0.45, 1.0),
            name="left_zone",
            is_static=True,
        )
        self.regions["left_zone"] = {
            "center": np.array([-0.15, -0.12, 0.742], dtype=np.float64),
            "size": np.array([0.16, 0.12, 0.004], dtype=np.float64),
            "tolerance": 0.04,
            "marker": self.left_zone_marker,
        }

        self.right_zone_marker = create_box(
            scene=self,
            pose=sapien.Pose([0.05, -0.12, 0.742], [1, 0, 0, 0]),
            half_size=(0.08, 0.06, 0.002),
            color=(0.1, 0.9, 0.2),
            name="right_zone",
            is_static=True,
        )
        self.regions["right_zone"] = {
            "center": np.array([0.05, -0.12, 0.742], dtype=np.float64),
            "size": np.array([0.16, 0.12, 0.004], dtype=np.float64),
            "tolerance": 0.04,
            "marker": self.right_zone_marker,
        }


    def play_once(self):
        green_block_pose = self.green_block.get_pose().p
        main_arm = ArmTag("left" if green_block_pose[0] < 0 else "right")

        self.move(self.grasp_actor(
            self.green_block,
            arm_tag=main_arm,
            pre_grasp_dis=0.09,
            grasp_dis=0.0,
        ))

        self.move(self.move_by_displacement(
            arm_tag=main_arm,
            x=0.0,
            y=0.0,
            z=0.08,
            move_axis="world",
        ))

        right_zone_marker = self.regions["right_zone"].get("marker")
        if right_zone_marker is not None:
            right_zone_pose = right_zone_marker.get_functional_point(1)
        else:
            right_zone_center = self.regions["right_zone"]["center"]
            right_zone_pose = right_zone_center.tolist() + [1, 0, 0, 0]
        self.move(self.place_actor(
            self.green_block,
            arm_tag=main_arm,
            target_pose=right_zone_pose,
            functional_point_id=0,
            pre_dis=0.05,
            dis=0.0,
            is_open=True,
            pre_dis_axis="fp",
        ))

        self.move(self.move_by_displacement(
            arm_tag=main_arm,
            x=0.0,
            y=0.0,
            z=0.06,
            move_axis="world",
        ))

        self.info["info"] = {
            '{A}': 'green block',
            '{B}': 'right zone',
            '{C}': '002_bowl/base3',
            '{a}': str(main_arm),
        }
        return self.info

    def check_success(self):
        checks = []
        green_block_pose = self.green_block.get_pose().p
        right_zone_region = self.regions["right_zone"]
        right_zone_center = right_zone_region["center"]
        right_zone_half = right_zone_region["size"][:2] / 2.0 + 0.04
        checks.append(np.all(np.abs(green_block_pose[:2] - right_zone_center[:2]) <= right_zone_half))
        blue_bowl_delta = np.linalg.norm(self.blue_bowl.get_pose().p - self._initial_positions["blue_bowl"])
        checks.append(blue_bowl_delta <= 0.03)
        checks.append(self.is_left_gripper_open() and self.is_right_gripper_open())
        return all(checks)
