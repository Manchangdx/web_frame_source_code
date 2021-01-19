from itertools import chain

from django.utils.itercompat import is_iterable


class Tags:
    """
    Built-in tags for internal checks.
    """
    admin = 'admin'
    async_support = 'async_support'
    caches = 'caches'
    compatibility = 'compatibility'
    database = 'database'
    models = 'models'
    security = 'security'
    signals = 'signals'
    staticfiles = 'staticfiles'
    templates = 'templates'
    translation = 'translation'
    urls = 'urls'


class CheckRegistry:

    def __init__(self):
        self.registered_checks = set()
        self.deployment_checks = set()

    def register(self, check=None, *tags, **kwargs):
        """
        Can be used as a function or a decorator. Register given function
        `f` labeled with given `tags`. The function should receive **kwargs
        and return list of Errors and Warnings.

        Example::

            registry = CheckRegistry()
            @registry.register('mytag', 'anothertag')
            def my_check(apps, **kwargs):
                # ... perform checks and collect `errors` ...
                return errors
            # or
            registry.register(my_check, 'mytag', 'anothertag')
        """
        def inner(check):
            # tags 可能是元组 ('models', )
            check.tags = tags
            checks = self.deployment_checks if kwargs.get('deploy') else self.registered_checks
            checks.add(check)
            return check

        if callable(check):
            return inner(check)
        else:
            if check:
                tags += (check,)
            return inner

    def run_checks(self, app_configs=None, tags=None, include_deployment_checks=False, databases=None):
        """
        Run all registered checks and return list of Errors and Warnings.
        """
        errors = []
        checks = self.get_checks(include_deployment_checks)
        #print('【django.core.checks.registry...run_checks】checks:')
        #for c in checks:
        #    print('\t', c)

        if tags is not None:
            checks = [check for check in checks if not set(check.tags).isdisjoint(tags)]

        # 下面这个 checks 变量是一个列表，里面是来自各个包中的 checks 模块中的函数：
        # django.core.checks.model_checks.check_lazy_references
        # django.core.checks.model_checks.check_all_models（接下来研究这个）
        # django.contrib.auth.checks.check_user_model（和这个）
        # django.core.checks.urls.check_url_config
        # django.core.checks.urls.check_url_namespaces_unique
        # ... ...
        # 我们分析的是 python manage.py makemigrations 命令，所以主要研究第 2 个函数
        for check in checks:
            new_errors = check(app_configs=app_configs, databases=databases)
            from django.contrib.auth.checks import check_user_model
            if check is check_user_model:
                print('-----cccc', check)
            #print('【django.core.checks.registry...run_checks】new_errors:', new_errors)
            assert is_iterable(new_errors), (
                "The function %r did not return a list. All functions registered "
                "with the checks registry must return a list." % check)
            errors.extend(new_errors)
        return errors

    def tag_exists(self, tag, include_deployment_checks=False):
        return tag in self.tags_available(include_deployment_checks)

    def tags_available(self, deployment_checks=False):
        return set(chain.from_iterable(
            check.tags for check in self.get_checks(deployment_checks)
        ))

    def get_checks(self, include_deployment_checks=False):
        checks = list(self.registered_checks)
        if include_deployment_checks:
            checks.extend(self.deployment_checks)
        return checks


registry = CheckRegistry()
register = registry.register
run_checks = registry.run_checks
tag_exists = registry.tag_exists
