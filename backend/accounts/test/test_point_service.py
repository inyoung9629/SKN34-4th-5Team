from django.db.models.deletion import ProtectedError
from django.test import TestCase

from accounts.models import CustomUser, PointTransaction, PointWallet
from accounts.point_service import grant_points


class PointServiceTests(TestCase):
    def test_grant_is_idempotent_and_updates_common_wallet(self):
        user = CustomUser.objects.create_user(username="point-user", password="safe-password")
        wallet, entry, created = grant_points(user=user, amount=123, source_type="test", source_key="case-1")
        same_wallet, same_entry, duplicate = grant_points(user=user, amount=123, source_type="test", source_key="case-1")

        self.assertTrue(created)
        self.assertFalse(duplicate)
        self.assertEqual(wallet.pk, same_wallet.pk)
        self.assertEqual(entry.pk, same_entry.pk)
        self.assertEqual(PointWallet.objects.get(user=user).balance, 123)
        self.assertEqual(PointTransaction.objects.filter(user=user).count(), 1)

    def test_point_ledger_protects_user_and_wallet_from_deletion(self):
        user = CustomUser.objects.create_user(username="protected-user", password="safe-password")
        wallet, entry, _ = grant_points(user=user, amount=10, source_type="test", source_key="protected")

        for instance in (user, wallet):
            with self.subTest(model=type(instance).__name__), self.assertRaises(ProtectedError):
                instance.delete()
        self.assertTrue(CustomUser.objects.filter(pk=user.pk).exists())
        self.assertEqual(PointWallet.objects.get(pk=wallet.pk).balance, 10)
        self.assertTrue(PointTransaction.objects.filter(pk=entry.pk).exists())
