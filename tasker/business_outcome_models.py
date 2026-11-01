from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class BusinessContribution(models.Model):
    """Explicit goal/team attribution, separate from a source transaction."""
    link = models.OneToOneField('tasker.BusinessRecordLink',on_delete=models.PROTECT,related_name='contribution')
    team = models.ForeignKey('tasker.Team',on_delete=models.PROTECT,null=True,blank=True)
    key_result = models.ForeignKey('tasker.KeyResult',on_delete=models.PROTECT,null=True,blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.team_id and (self.team.company_id != self.link.company_id or self.team.is_deleted):
            raise ValidationError('Choose a live team in the source company.')
        if self.key_result_id and (self.key_result.goal.company_id != self.link.company_id or (
                self.key_result.goal.team_id and self.key_result.goal.team_id != self.team_id)):
            raise ValidationError('The result must match the source company and attributed team.')


class BusinessOutcomeEvidence(models.Model):
    """One output per canonical action; changes cannot create a second credit."""
    link = models.OneToOneField('tasker.BusinessRecordLink',on_delete=models.PROTECT,related_name='outcome_evidence')
    outcome = models.OneToOneField('tasker.OutcomeMeasurement',on_delete=models.PROTECT,related_name='business_evidence')
    source_version = models.CharField(max_length=64)
    source_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self,*args,**kwargs):
        if not self._state.adding:
            raise ValidationError('Business outcome evidence is immutable. Retract invalid evidence explicitly.')
        return super().save(*args,**kwargs)
