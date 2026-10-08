import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from rest_framework.test import APITestCase

from .image_storage import ObjectNotFound, StorageUnavailable
from .images import MAX_BYTES, MAX_REQUEST_BYTES, _normalize
from .models import CommunityDraft, CommunityImage, CommunityPost


def image_file(
    image_format="PNG", *, content_type=None, size=(8, 6), mode="RGB", exif=None, save_all=False, save_options=None
):
    output = io.BytesIO()
    image = Image.new(mode, size, 0 if mode == "1" else "red")
    kwargs = {"exif": exif} if exif else {}
    kwargs.update(save_options or {})
    if save_all:
        kwargs.update(save_all=True, append_images=[Image.new("RGB", size, "blue")])
    image.save(output, image_format, **kwargs)
    mime = content_type or {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[image_format]
    return SimpleUploadedFile(f"test.{image_format.lower()}", output.getvalue(), content_type=mime)


class ImageValidationTests(SimpleTestCase):
    def test_accepts_supported_still_formats(self):
        for image_format in ("JPEG", "PNG", "WEBP"):
            with self.subTest(image_format=image_format):
                body, content_type, extension, width, height = _normalize(image_file(image_format))
                self.assertLessEqual(len(body), MAX_BYTES)
                self.assertEqual((width, height), (8, 6))
                self.assertEqual(content_type, f"image/{'jpeg' if image_format == 'JPEG' else image_format.lower()}")
                self.assertEqual(extension, "jpg" if image_format == "JPEG" else image_format.lower())

    def test_rejects_size_pixel_and_animation_bounds(self):
        oversized = SimpleUploadedFile("large.jpg", b"x" * (MAX_BYTES + 1), content_type="image/jpeg")
        with self.assertRaisesRegex(ValueError, "5MiB"):
            _normalize(oversized)

        too_many_pixels = image_file("PNG", size=(5000, 4001), mode="1")
        with self.assertRaisesRegex(ValueError, "2천만"):
            _normalize(too_many_pixels)

        with self.assertRaisesRegex(ValueError, "정지"):
            _normalize(image_file("WEBP", save_all=True))

    def test_rejects_corrupt_svg_and_spoofed_content_type(self):
        for upload in (
            SimpleUploadedFile("bad.png", b"not an image", content_type="image/png"),
            SimpleUploadedFile("vector.svg", b"<svg/>", content_type="image/png"),
            image_file("PNG", content_type="image/jpeg"),
        ):
            with self.subTest(name=upload.name), self.assertRaises(ValueError):
                _normalize(upload)

    def test_reencode_removes_exif_and_applies_orientation(self):
        exif = Image.Exif()
        exif[274] = 6
        exif[305] = "private-camera-data"
        body, _, _, width, height = _normalize(image_file("JPEG", size=(4, 2), exif=exif))

        with Image.open(io.BytesIO(body)) as normalized:
            self.assertEqual((width, height), (2, 4))
            self.assertEqual(dict(normalized.getexif()), {})

    def test_reencode_removes_png_text_webp_xmp_and_handles_bomb_errors(self):
        png_info = PngInfo()
        png_info.add_text("private", "metadata")
        png, *_ = _normalize(image_file("PNG", save_options={"pnginfo": png_info}))
        webp, *_ = _normalize(image_file("WEBP", save_options={"xmp": b"private-xmp"}))
        with Image.open(io.BytesIO(png)) as normalized_png, Image.open(io.BytesIO(webp)) as normalized_webp:
            self.assertNotIn("private", normalized_png.info)
            self.assertNotIn("xmp", normalized_webp.info)

        with patch.object(Image, "MAX_IMAGE_PIXELS", 1), self.assertRaisesRegex(ValueError, "손상"):
            _normalize(image_file())


class ImageApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username="image-owner")
        cls.other = get_user_model().objects.create_user(username="image-other")
        cls.post = CommunityPost.objects.create(
            source_id="image-post",
            post_number="999997",
            board="free",
            team_code="",
            owner=cls.owner,
            author="owner",
            title="image post",
            content="body",
            category="잡담",
        )
        cls.draft = CommunityDraft.objects.create(owner=cls.owner, board="free")

    def setUp(self):
        self.objects = {}
        self.put_patch = patch("community.images.put_object", side_effect=self._put).start()
        self.get_patch = patch("community.images.get_object", side_effect=self._get).start()
        self.delete_patch = patch("community.images.delete_object", side_effect=self._delete).start()
        self.addCleanup(patch.stopall)

    def _put(self, key, body, content_type):
        self.objects[key] = body

    def _get(self, key):
        return io.BytesIO(self.objects[key])

    def _delete(self, key):
        self.objects.pop(key, None)

    def create_image(self, *, draft=None, post=None):
        body, content_type, _, width, height = _normalize(image_file())
        key = f"community/test/{len(self.objects)}.png"
        self.objects[key] = body
        return CommunityImage.objects.create(
            owner=self.owner,
            draft=draft,
            post=post,
            object_key=key,
            content_type=content_type,
            size=len(body),
            width=width,
            height=height,
        )

    def test_upload_requires_auth_and_returns_validated_metadata(self):
        url = "/api/v1/community/images/"
        self.assertEqual(self.client.post(url, {"image": image_file()}, format="multipart").status_code, 401)
        self.client.force_authenticate(self.owner)
        response = self.client.post(url, {"image": image_file()}, format="multipart")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.data), {"id", "contentType", "size", "width", "height", "createdAt", "url"})
        image = CommunityImage.objects.get(pk=response.data["id"])
        self.assertEqual(image.owner, self.owner)
        self.assertTrue(image.object_key.startswith("community/"))
        self.assertNotIn("test.png", image.object_key)
        self.assertEqual(self.objects[image.object_key].startswith(b"\x89PNG"), True)

    def test_upload_rejects_oversized_or_ambiguous_multipart_before_storage(self):
        self.client.force_authenticate(self.owner)
        oversized = self.client.post(
            "/api/v1/community/images/",
            {"image": image_file()},
            format="multipart",
            CONTENT_LENGTH=str(MAX_REQUEST_BYTES + 1),
        )
        extra = self.client.post(
            "/api/v1/community/images/",
            {"image": image_file(), "unknown": "value"},
            format="multipart",
        )
        multiple = self.client.post(
            "/api/v1/community/images/",
            {"image": [image_file(), image_file()]},
            format="multipart",
        )
        self.assertEqual((oversized.status_code, extra.status_code, multiple.status_code), (413, 400, 400))
        self.put_patch.assert_not_called()

    def test_storage_errors_are_safe_and_missing_object_is_404(self):
        self.client.force_authenticate(self.owner)
        with patch("community.images.put_object", side_effect=StorageUnavailable):
            response = self.client.post("/api/v1/community/images/", {"image": image_file()}, format="multipart")
        self.assertEqual((response.status_code, response.data), (503, {"detail": "이미지 저장소를 사용할 수 없습니다."}))

        image = self.create_image()
        with patch("community.images.get_object", side_effect=ObjectNotFound):
            response = self.client.get(f"/api/v1/community/images/{image.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_private_owner_read_and_published_public_read(self):
        private = self.create_image()
        private_url = f"/api/v1/community/images/{private.pk}/"
        self.assertEqual(self.client.get(private_url).status_code, 404)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(private_url).status_code, 404)
        self.client.force_authenticate(self.owner)
        owner_response = self.client.get(private_url)
        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(b"".join(owner_response.streaming_content), self.objects[private.object_key])
        self.assertEqual(owner_response["Cache-Control"], "private, no-store")

        draft = self.create_image(draft=self.draft)
        draft_url = f"/api/v1/community/images/{draft.pk}/"
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(draft_url).status_code, 404)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(draft_url).status_code, 200)

        public = self.create_image(post=self.post)
        self.client.force_authenticate(None)
        public_response = self.client.get(f"/api/v1/community/images/{public.pk}/")
        self.assertEqual(public_response.status_code, 200)
        self.assertEqual(public_response["Cache-Control"], "public, max-age=60")

    def test_delete_is_owner_only_and_refuses_published_attachment(self):
        image = self.create_image()
        url = f"/api/v1/community/images/{image.pk}/"
        self.assertEqual(self.client.delete(url).status_code, 401)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.delete(url).status_code, 403)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertFalse(CommunityImage.objects.filter(pk=image.pk).exists())
        self.assertNotIn(image.object_key, self.objects)

        published = self.create_image(post=self.post)
        self.assertEqual(self.client.delete(f"/api/v1/community/images/{published.pk}/").status_code, 409)
        self.assertTrue(CommunityImage.objects.filter(pk=published.pk).exists())
        self.assertIn(published.object_key, self.objects)

    def test_delete_storage_failure_rolls_back_database_row(self):
        image = self.create_image()
        self.client.force_authenticate(self.owner)
        with patch("community.images.delete_object", side_effect=StorageUnavailable):
            response = self.client.delete(f"/api/v1/community/images/{image.pk}/")
        self.assertEqual(response.status_code, 503)
        self.assertTrue(CommunityImage.objects.filter(pk=image.pk).exists())

    def test_account_deletion_retains_image_metadata(self):
        private = self.create_image()
        public = self.create_image(post=self.post)

        self.owner.delete()
        private.refresh_from_db()
        public.refresh_from_db()

        self.assertIsNone(private.owner_id)
        self.assertIsNone(public.owner_id)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f"/api/v1/community/images/{private.pk}/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/community/images/{public.pk}/").status_code, 200)

    def test_db_failure_compensates_uploaded_object(self):
        self.client.force_authenticate(self.owner)
        self.client.raise_request_exception = False
        with patch("community.images.CommunityImage.objects.create", side_effect=RuntimeError("db failed")):
            response = self.client.post("/api/v1/community/images/", {"image": image_file()}, format="multipart")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.objects, {})
