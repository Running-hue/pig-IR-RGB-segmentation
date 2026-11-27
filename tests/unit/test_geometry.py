"""Geometric checks: known projections, immutable depth, safe legacy handling."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest

from backend.core import Image,np,cv2,atomic_json,discover,png_bytes
from backend.dataset import Dataset
from backend.geometry import (prepare_frame,remap_grid,camera,backproject_mask,RGB_VERSION,
                      RGB_LEGACY,validate_mask_region,write_geometry,normalized_rays)
from tests.fixtures.geometry import profile,calibration


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.folder=self.root/'source'/'pig_1'/'frame';self.folder.mkdir(parents=True)
        y,x=np.indices((90,120))
        self.image=np.stack([x*2,y*2,(x+y)%256],axis=-1).astype(np.uint8)
        Image.fromarray(self.image).save(self.folder/'color.png')
        self.depth=np.full((60,80),1300,np.uint16)
        np.save(self.folder/'depth_aligned_to_color_mm.npy',self.depth)
        ir=np.where(((x-60)/40)**2+((y-45)/20)**2<1,1000,20).astype(np.uint16)
        np.save(self.folder/'infrared.npy',ir);Image.fromarray(ir).save(self.folder/'infrared.png')
        np.save(self.folder/'depth_mm.npy',np.full(ir.shape,900,np.uint16))
        # Source and target differ in resolution AND intrinsics, while sharing a camera frame.
        self.source=profile(120,90,{'k1':.16,'k2':-.03,'p1':.004,'p2':-.002},fx=100,cx=62)
        self.target=profile(80,60,fx=70,cx=39)
        self.data=calibration(self.folder,rgb_size=(120,90),ir_size=(120,90),source=self.source,target=self.target)

    def tearDown(self):self.tmp.cleanup()

    def prepare(self):return prepare_frame(self.folder,'rgb')

    def test_rgb_mapping_matches_analytic_projection_on_known_ray(self):
        before={f.name:f.read_bytes() for f in self.folder.iterdir()}
        image,depth,g,valid=self.prepare()
        self.assertEqual(image.shape,(60,80,3));self.assertTrue(np.array_equal(depth,self.depth))
        self.assertEqual(g['coordinate_space'],'aligned_color');self.assertTrue(g['pointcloud_ready'])
        self.assertEqual(g['camera_matrix'],camera(self.target)[0].tolist())
        self.assertFalse(g['extrinsics_applied_to_saved_aligned_depth'])
        # Explicit Brown projection of target pixel (65,40), independent of remap generation.
        kt,_,_=camera(self.target);ks,_,_=camera(self.source)
        u,v=65,40;xn=(u-kt[0,2])/kt[0,0];yn=(v-kt[1,2])/kt[1,1];r2=xn*xn+yn*yn
        radial=1+.16*r2-.03*r2*r2
        xd=xn*radial+2*.004*xn*yn-.002*(r2+2*xn*xn)
        yd=yn*radial+.004*(r2+2*yn*yn)+2*(-.002)*xn*yn
        sx=ks[0,0]*xd+ks[0,2];sy=ks[1,1]*yd+ks[1,2]
        # Source image encodes x and y as color ramps, so interpolation must match projection.
        self.assertTrue(valid[v,u]);self.assertLess(abs(float(image[v,u,0])-2*sx),1.1)
        self.assertLess(abs(float(image[v,u,1])-2*sy),1.1)
        self.assertEqual(before,{f.name:f.read_bytes() for f in self.folder.iterdir()})

    def test_target_distortion_is_preserved_when_saved_aligned_grid_has_it(self):
        target=profile(80,60,{'k1':.08,'p1':.001},fx=70,cx=39)
        calibration(self.folder,source=self.source,target=target,ir_size=(120,90))
        image,depth,g,valid=self.prepare()
        pixels=np.array([[[10.,15.]],[[45.,30.]],[[65.,45.]]])
        kt,dt,_=camera(target);ks,ds,_=camera(self.source)
        rays=normalized_rays(pixels,kt,dt)
        objects=np.column_stack([rays,np.ones(len(rays))])
        projected,_=cv2.projectPoints(objects,np.zeros(3),np.zeros(3),ks,ds)
        mx,my,_=remap_grid(json.dumps(g['source_profile'],sort_keys=True),json.dumps(g['depth_profile'],sort_keys=True))
        for (u,v),(sx,sy) in zip(pixels.reshape(-1,2).astype(int),projected.reshape(-1,2)):
            self.assertAlmostEqual(float(mx[v,u]),sx,places=4);self.assertAlmostEqual(float(my[v,u]),sy,places=4)

    def test_equal_profiles_do_not_undistort_twice(self):
        target=copy.deepcopy(self.source)
        calibration(self.folder,source=self.source,target=target,ir_size=(120,90))
        np.save(self.folder/'depth_aligned_to_color_mm.npy',np.full((90,120),1300,np.uint16))
        image,_,g,valid=self.prepare()
        self.assertEqual(g['image_transform'],'identity_profile_match')
        self.assertTrue(np.array_equal(image,self.image));self.assertTrue(valid.all())

    def test_rgb_backprojection_uses_target_intrinsic_scale_and_zero_extrinsic(self):
        # Source sensor codes must be scaled with the ALIGNED frame's own scale.
        self.data['alignment']['output_depth_frame']['value_scale_mm_per_code']=.25
        atomic_json(self.folder/'reconstruction.json',self.data)
        np.save(self.folder/'depth_aligned_to_color_code_u16.npy',np.full((60,80),5201,np.uint16))
        _,_,g,valid=self.prepare();mask=np.zeros((60,80),np.uint8);mask[30,39]=255;mask[30,46]=255
        xyz,uv=backproject_mask(self.folder,mask,g)
        self.assertTrue(np.array_equal(uv,np.array([[39,30],[46,30]])))
        self.assertTrue(np.allclose(xyz[:,2],1300.25))
        self.assertAlmostEqual(xyz[0,0],0.);self.assertAlmostEqual(xyz[1,0],130.025)
        self.assertTrue(np.allclose(xyz[:,1],0.))
        projected,_=cv2.projectPoints(xyz,np.zeros(3),np.zeros(3),np.array(g['camera_matrix']),np.array(g['distortion_coefficients_opencv']))
        self.assertTrue(np.allclose(projected.reshape(-1,2),uv,atol=1e-6))

    def test_ir_keeps_original_depth_grid_and_unprojects_original_distortion(self):
        raw=profile(120,90,{'k1':.2,'p1':.002})
        self.data['selected_capture']['streams']['depth']['profile']=raw
        self.data['selected_capture']['streams']['infrared']['profile']=raw
        atomic_json(self.folder/'reconstruction.json',self.data)
        image,depth,g,valid=prepare_frame(self.folder,'infrared')
        self.assertEqual(image.shape,(90,120));self.assertEqual(g['coordinate_space'],'depth_native')
        mask=np.zeros(image.shape,np.uint8);mask[30:60:10,40:90:10]=255
        xyz,uv=backproject_mask(self.folder,mask,g)
        projected,_=cv2.projectPoints(xyz,np.zeros(3),np.zeros(3),np.array(g['camera_matrix']),np.array(g['distortion_coefficients_opencv']))
        self.assertTrue(np.allclose(projected.reshape(-1,2),uv,atol=1e-5))
        # Nonzero distortion requires inverse rays, not the simple pinhole formula.
        k=np.array(g['camera_matrix']);naive_x=(uv[:,0]-k[0,2])/k[0,0]*900
        self.assertGreater(float(np.max(np.abs(xyz[:,0]-naive_x))),.1)

    def test_outside_sampling_footprint_is_marked_and_cannot_be_saved(self):
        _,_,g,valid=self.prepare();self.assertFalse(valid.all())
        mask=np.zeros(valid.shape,np.uint8);mask[~valid]=255
        with self.assertRaisesRegex(ValueError,'无效边缘'):validate_mask_region(mask,valid)
        with self.assertRaises(ValueError):backproject_mask(self.folder,mask,g)
        out=self.root/'geometry';write_geometry(out,g,valid)
        self.assertEqual(json.loads((out/'geometry.json').read_text())['geometry_id'],g['geometry_id'])
        self.assertTrue(np.array_equal(np.asarray(Image.open(out/'image_valid_mask.png'))>0,valid))

    def test_missing_or_inconsistent_profiles_are_not_treated_as_same_size_alignment(self):
        for mutate in [
            lambda d:d['alignment'].pop('output_depth_frame'),
            lambda d:d['selected_capture']['streams']['color']['profile']['distortion'].update(model_name='FISHEYE'),
            lambda d:d['alignment'].update(target_color_identity={'sdk_frame_index':'other'}),
            lambda d:d['alignment'].update(operation='unknown'),
        ]:
            d=copy.deepcopy(self.data);mutate(d);atomic_json(self.folder/'reconstruction.json',d)
            with self.assertRaises(ValueError):self.prepare()
        (self.folder/'reconstruction.json').unlink()
        with self.assertRaisesRegex(ValueError,'reconstruction.json'):self.prepare()

    def test_ir_extrinsic_mismatch_is_rejected_even_with_equal_intrinsics(self):
        self.data['actual_profile_extrinsics']['infrared_to_color']=copy.deepcopy(self.data['actual_profile_extrinsics']['infrared_to_color'])
        self.data['actual_profile_extrinsics']['infrared_to_color']['translation_mm'][0]=25
        atomic_json(self.folder/'reconstruction.json',self.data)
        with self.assertRaisesRegex(ValueError,'不在同一相机坐标'):prepare_frame(self.folder,'infrared')

    def test_changed_calibration_or_depth_cannot_backproject_an_old_mask(self):
        _,_,g,valid=self.prepare();mask=valid.astype(np.uint8)*255
        np.save(self.folder/'depth_aligned_to_color_mm.npy',self.depth+1)
        with self.assertRaisesRegex(ValueError,'发生变化'):backproject_mask(self.folder,mask,g)

    def test_invalid_ir_extrinsic_shape_or_nan_is_rejected(self):
        for values in [[0.], [float('nan'),0.,0.]]:
            d=copy.deepcopy(self.data)
            d['actual_profile_extrinsics']['infrared_to_color']['translation_mm']=values
            atomic_json(self.folder/'reconstruction.json',d)
            with self.assertRaisesRegex(ValueError,'外参尺寸或数值无效'):
                prepare_frame(self.folder,'infrared')

    def test_legacy_rgb_results_remain_native_and_read_only_for_geometry_edits(self):
        out=self.root/'legacy';out.mkdir()
        rows=discover(self.root/'source','rgb')
        atomic_json(out/'dataset.json',{'source':str(self.root/'source'),'input_mode':'rgb','frames':rows})
        d=Dataset(self.root/'source',out);row=d.rows[0]
        dest=d.dest(row);mask=np.full((90,120),255,np.uint8)
        atomic_json(dest/'segmentation.json',{'status':'draft','input_mode':'rgb','stats':{'valid_depth_ratio':1.}})
        (dest/'trunk_mask_draft.png').write_bytes(png_bytes(mask))
        detail=d.detail(row['id'])
        self.assertTrue(detail['geometry_legacy']);self.assertEqual(detail['width'],120)
        self.assertFalse(detail['geometry']['pointcloud_ready']);self.assertIsNone(detail['stats']['valid_depth_ratio'])
        before=(dest/'trunk_mask_draft.png').read_bytes()
        with self.assertRaisesRegex(ValueError,'旧 RGB'):d.predict_sam({'id':row['id']})
        with self.assertRaisesRegex(ValueError,'旧 RGB'):
            d.save({'id':row['id'],'mask':'data:image/png;base64,'+base64.b64encode(before).decode()})
        self.assertEqual(before,(dest/'trunk_mask_draft.png').read_bytes())


if __name__=='__main__':unittest.main()
