from django.utils.timezone import now

from .. import Event, ThisObject, mixins, signals
from ..activation import Activation, STATUS, all_leading_canceled


class HandlerActivation(Activation):
    def execute(self):
        self.flow_task.handler(self)

    @Activation.status.transition(source=STATUS.NEW)
    def perform(self):
        with self.exception_guard():
            self.task.started = now()

            signals.task_started.send(sender=self.flow_class, process=self.process, task=self.task)

            self.execute()

            self.task.finished = now()
            self.set_status(STATUS.DONE)
            self.task.save()

            signals.task_finished.send(sender=self.flow_class, process=self.process, task=self.task)

            self.activate_next()

    @Activation.status.transition(source=STATUS.ERROR)
    def retry(self):
        """
        Retry the next node calculation and activation
        """
        self.perform.original()

    @Activation.status.transition(source=[STATUS.ERROR, STATUS.DONE], target=STATUS.NEW)
    def undo(self):
        """
        Undo the task
        """
        super(HandlerActivation, self).undo.original()

    @Activation.status.transition(source=STATUS.DONE, conditions=[all_leading_canceled])
    def activate_next(self):
        """Activate all outgoing edges."""
        if self.flow_task._next:
            self.flow_task._next.activate(prev_activation=self, token=self.task.token)

    @classmethod
    def activate(cls, flow_task, prev_activation, token):
        """Instantiate new task."""
        task = flow_task.flow_class.task_class(
            process=prev_activation.process,
            flow_task=flow_task,
            token=token)

        task.save()
        task.previous.add(prev_activation.task)

        activation = cls()
        activation.initialize(flow_task, task)
        activation.perform()

        return activation


class Handler(mixins.TaskDescriptionMixin,
              mixins.NextNodeMixin,
              mixins.DetailViewMixin,
              mixins.UndoViewMixin,
              mixins.CancelViewMixin,
              mixins.PerformViewMixin,
              Event):

    task_type = 'FUNC'
    activation_class = HandlerActivation

    def __init__(self, handler, **kwargs):
        self.handler = handler
        super(Handler, self).__init__(**kwargs)

    def ready(self):
        if isinstance(self.handler, ThisObject):
            self.handler = getattr(self.flow_class.instance, self.handler.name)
